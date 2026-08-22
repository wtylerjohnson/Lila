"""SEC FY2026 Procurement Forecast (official PDF).

The SEC file is an incumbent-backed forecast census: Business Lead,
requirement, incumbent and PIID, an opaque obligated-dollar letter, incumbent
period, contract type, PSC, NAICS, and size determination.  It does *not*
publish a legend for the dollar letters or anticipated solicitation/award
dates, so neither is invented by this adapter.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from agents.schemas import RawOpportunity
from tools.api.base import (
    DataSource, SourceKind, SourceQuery, cap_disclosed, register_source,
)
from tools.api.pdf_forecast import (
    clean, fetch_pdf, http_date_as_of, pdf_tables, stated,
)
from tools.api.provenance import ProvenanceEnvelope
from tools.text_match import matching_phrases


LOG = logging.getLogger(__name__)
SOURCE_NAME = "sec_procurement_forecast"
AGENCY = "Securities and Exchange Commission"
PDF_URL = "https://www.sec.gov/files/fy2026-sec-procurement-forecast.pdf"
MODE = "live_sec_fy2026_procurement_forecast_pdf"

_COLUMNS = (
    "business_lead", "requirement", "incumbent_contractor",
    "incumbent_contract_id", "obligated_dollars_code",
    "incumbent_start_date", "incumbent_end_date", "contract_type", "psc",
    "naics", "incumbent_size_determination",
)
_NAICS = re.compile(r"(?<!\d)\d{6}(?!\d)")
_PSC = re.compile(r"\b[A-Z0-9]{4}\b", re.I)


def _rows_from_pdf(blob: bytes) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in pdf_tables(blob):
        for values in table:
            if len(values) != len(_COLUMNS):
                continue
            title = clean(values[1])
            if not title or title.casefold() == "requirement":
                continue
            rows.append({key: clean(value)
                         for key, value in zip(_COLUMNS, values)})
    if not rows:
        raise ValueError("SEC PDF has no recognizable 11-column forecast rows")
    return rows


def _source_id(row: dict[str, str]) -> str:
    title = clean(row.get("requirement"))
    title_hash = hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()[:10]
    piid = stated(row.get("incumbent_contract_id"))
    if piid:
        key = re.sub(r"[^A-Z0-9]+", "", piid.upper())
        return f"sec-fy2026-incumbent-{key}-{title_hash}"
    basis = "\x1f".join((clean(row.get("business_lead")), title,
                         clean(row.get("incumbent_end_date"))))
    return "sec-fy2026-composite-" + hashlib.sha256(
        basis.casefold().encode("utf-8")).hexdigest()[:20]


def _agency_allowed(query: SourceQuery) -> bool:
    if not query.agencies:
        return True
    wanted = " ".join(query.agencies).casefold()
    return any(token in wanted for token in (
        "sec", "securities and exchange commission",
    ))


def _matches(row: dict[str, str], query: SourceQuery) -> bool:
    if not _agency_allowed(query):
        return False
    haystack = " ".join(row.values())
    if query.keywords and not matching_phrases(haystack, query.keywords):
        return False
    naics = stated(row.get("naics")) or ""
    if query.naics_codes and not any(
            naics.startswith(str(code)) for code in query.naics_codes):
        return False
    psc = (stated(row.get("psc")) or "").upper()
    if query.psc_codes and not any(
            psc.startswith(str(code).upper()) for code in query.psc_codes):
        return False
    return True


def _normalize_rows(
    rows: list[dict[str, str]], query: SourceQuery,
) -> tuple[list[RawOpportunity], int]:
    records: list[RawOpportunity] = []
    dropped = 0
    for row in rows:
        title = stated(row.get("requirement"))
        if not title:
            dropped += 1
            continue
        if not _matches(row, query):
            continue
        native = {key: clean(row.get(key)) for key in _COLUMNS}
        normalized = {
            **native,
            "component": stated(row.get("business_lead")),
            "incumbent_stated": stated(row.get("incumbent_contractor")),
            "incumbent_contract_stated": stated(
                row.get("incumbent_contract_id")),
            # The publisher supplies A-E without a public legend.  This stays
            # an opaque code and never becomes a dollar value or value band.
            "obligated_dollars_code": stated(row.get("obligated_dollars_code")),
            "anticipated_solicitation": None,
            "anticipated_award": None,
            "forecast_status": "published in SEC FY2026 procurement forecast",
            "_join_keys": {
                "incumbent_contract_id": stated(row.get("incumbent_contract_id")),
                "naics": next(iter(_NAICS.findall(clean(row.get("naics")))), None),
                "psc": next(iter(_PSC.findall(clean(row.get("psc")))), None),
                "business_lead": stated(row.get("business_lead")),
            },
        }
        records.append(RawOpportunity(
            source=SOURCE_NAME,
            source_id=_source_id(row),
            title=title,
            agency=AGENCY,
            naics_code=normalized["_join_keys"]["naics"],
            psc_code=normalized["_join_keys"]["psc"],
            # Size determination describes the incumbent; it is not a future
            # set-aside decision.  Likewise, A-E is not a numeric estimate.
            set_aside=None,
            estimated_value=None,
            api_url=PDF_URL,
            raw_payload={
                "_normalized": normalized,
                "_citation_url": PDF_URL,
                "_evidence_tier": "PROGRAM",
            },
        ))
    return records, dropped


class SecProcurementForecastSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT
    enabled = True

    def __init__(
        self,
        fetch_bytes: Optional[Callable[..., tuple[bytes, dict]]] = None,
    ) -> None:
        from tools.toggles import is_enabled
        self.enabled = is_enabled(self.name, True)
        self._fetch_bytes = fetch_bytes
        self.last_provenance: Optional[dict] = None

    def healthcheck(self) -> tuple[bool, str]:
        try:
            blob, _headers = (self._fetch_bytes or fetch_pdf)(
                PDF_URL, timeout=20.0, retries=1)
            rows = _rows_from_pdf(blob)
            return True, f"SEC FY2026 forecast reachable ({len(rows)} parsed rows)"
        except Exception as exc:  # noqa: BLE001
            return False, f"SEC FY2026 forecast unavailable: {exc}"

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        headers: dict = {}
        try:
            blob, headers = (self._fetch_bytes or fetch_pdf)(PDF_URL)
            rows = _rows_from_pdf(blob)
        except Exception as exc:  # noqa: BLE001
            self.last_provenance = ProvenanceEnvelope(
                source=self.name, status="failed", mode=MODE,
                retrieval_mode="live", retrieved_at=datetime.now(timezone.utc),
                record_count=0,
                limitations=[f"forecast retrieval failed: {exc}"],
                retrieval_url=PDF_URL,
            ).model_dump(mode="json")
            LOG.warning("SEC procurement forecast retrieval failed: %s", exc)
            return []

        matched, dropped = _normalize_rows(rows, query)
        capped = cap_disclosed(matched, query.limit or len(matched), key="items")
        records = capped["items"]
        limitations = [
            "SEC forecast rows are planning evidence, not live solicitations",
            "The PDF states incumbent period dates but no anticipated solicitation or award dates",
            "Obligated-dollar letters A-E are preserved as opaque source codes because the PDF supplies no legend",
            "Incumbent size determination is not treated as a future set-aside decision",
        ]
        if dropped:
            limitations.append(f"dropped {dropped} malformed or untitled rows")
        if capped.get("truncated"):
            limitations.append(str(capped["truncated_note"]))
        self.last_provenance = ProvenanceEnvelope(
            source=self.name, status="partial" if dropped else "complete",
            mode=MODE, retrieval_mode="live",
            retrieved_at=datetime.now(timezone.utc),
            data_as_of=http_date_as_of(headers), record_count=len(records),
            limitations=limitations,
            public_detail="Live official SEC FY2026 Procurement Forecast PDF",
            retrieval_url=PDF_URL,
            raw_content_sha256=hashlib.sha256(blob).hexdigest(),
            raw_content_kind="application/pdf",
            normalization_version="sec_procurement_forecast.v1.2026-08-08",
        ).model_dump(mode="json")
        self.last_provenance.update({
            "source_url": PDF_URL,
            "landing_url": PDF_URL,
        })
        for record in records:
            record.raw_payload["_provenance"] = self.last_provenance
        return records

    def forecasts(self):
        from tools.api.forecasts.bridge import from_raw
        records = self.search(SourceQuery(limit=5000))
        return [from_raw(record, self.last_provenance) for record in records]


try:
    register_source(SecProcurementForecastSource)
except ValueError:
    pass
