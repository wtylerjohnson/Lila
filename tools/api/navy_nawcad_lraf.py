"""NAWCAD FY26 Long-Range Acquisition Forecast (official PDF).

This Aircraft Division forecast is a 14-column planning table with mission
organization, requirement, PCO, incumbent/PIID, value, vehicle, projected
RFP/award timing, and socioeconomic designation.  Starred cells are revisions;
they are preserved in native payloads and stripped only from typed projections.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from agents.schemas import OpportunityContact, RawOpportunity
from tools.api.base import (
    DataSource, SourceKind, SourceQuery, cap_disclosed, register_source,
)
from tools.api.pdf_forecast import clean, fetch_pdf, pdf_tables, stated
from tools.api.provenance import ProvenanceEnvelope
from tools.text_match import matching_phrases


LOG = logging.getLogger(__name__)
SOURCE_NAME = "navy_nawcad_lraf"
AGENCY = "Department of the Navy / NAWCAD"
PDF_URL = (
    "https://www.navair.navy.mil/osbp/sites/g/files/jejdrs551/files/"
    "document/%5Bfilename%5D/NAWCAD%20LRAF%20Oct25-Feb26_Final2.pdf"
)
MODE = "live_nawcad_fy26_lraf_pdf"
DATA_AS_OF = "2026-02-05"

_COLUMNS = (
    "mission_organization", "title", "description", "pco",
    "incumbent_contractor", "incumbent_contract_number", "ordering_period",
    "value_range", "acquisition_strategy", "rfp_fy", "rfp_quarter_or_date",
    "award_fy", "award_quarter", "socioeconomic_designation",
)
_MONEY = re.compile(
    r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(K|M|B|thousand|million|billion)\b",
    re.I,
)
_MULTIPLIER = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6,
    "b": 1e9, "billion": 1e9,
}


def _rows_from_pdf(blob: bytes) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in pdf_tables(blob):
        for values in table:
            if len(values) != len(_COLUMNS):
                continue
            title = clean(values[1])
            if not title or title.casefold() == "title":
                continue
            # Front matter and the two-line headers have no requirement title.
            rows.append({key: clean(value)
                         for key, value in zip(_COLUMNS, values)})
    if not rows:
        raise ValueError("NAWCAD PDF has no recognizable 14-column LRAF rows")
    return rows


def _typed(value: Any) -> Optional[str]:
    value = clean(value).rstrip("*").strip()
    return stated(value)


def _source_id(row: dict[str, str]) -> str:
    title = clean(row.get("title")).rstrip("*").strip()
    title_hash = hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()[:10]
    contract = _typed(row.get("incumbent_contract_number"))
    if contract:
        key = re.sub(r"[^A-Z0-9]+", "", contract.upper())
        return f"nawcad-lraf-incumbent-{key}-{title_hash}"
    basis = "\x1f".join((title, clean(row.get("mission_organization")),
                         clean(row.get("rfp_fy")),
                         clean(row.get("rfp_quarter_or_date"))))
    return "nawcad-lraf-composite-" + hashlib.sha256(
        basis.casefold().encode("utf-8")).hexdigest()[:20]


def _lower_bound(value: Any) -> Optional[float]:
    text = clean(value)
    if not text or text.casefold().startswith(("below", "under", "<")):
        return None
    match = _MONEY.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", "")) * _MULTIPLIER[
        match.group(2).casefold()]


def _timing(fy: Any, quarter_or_date: Any) -> Optional[str]:
    fy_value = _typed(fy)
    period = _typed(quarter_or_date)
    if fy_value and period:
        return f"{fy_value} {period}"
    return period or fy_value


def _set_aside(value: Any) -> Optional[str]:
    designation = _typed(value)
    if not designation or designation.casefold() == "unrestricted":
        return None
    return designation


def _agency_allowed(query: SourceQuery) -> bool:
    if not query.agencies:
        return True
    wanted = " ".join(query.agencies).casefold()
    return any(token in wanted for token in (
        "navy", "nawcad", "aircraft division", "department of defense", "dod",
    ))


def _matches(row: dict[str, str], query: SourceQuery) -> bool:
    if not _agency_allowed(query):
        return False
    haystack = " ".join(row.values())
    if query.keywords and not matching_phrases(haystack, query.keywords):
        return False
    if query.set_asides:
        designation = clean(row.get("socioeconomic_designation")).casefold()
        if not any(clean(value).casefold() in designation
                   for value in query.set_asides):
            return False
    # The publisher does not state NAICS or PSC.  Do not infer either from title.
    return True


def _normalize_rows(
    rows: list[dict[str, str]], query: SourceQuery,
) -> tuple[list[RawOpportunity], int]:
    records: list[RawOpportunity] = []
    dropped = 0
    for row in rows:
        title = _typed(row.get("title"))
        if not title:
            dropped += 1
            continue
        if not _matches(row, query):
            continue
        native = {key: clean(row.get(key)) for key in _COLUMNS}
        revised = any("*" in value for value in native.values())
        normalized = {
            **native,
            "title": title,
            "component": _typed(row.get("mission_organization")),
            "incumbent_stated": _typed(row.get("incumbent_contractor")),
            "incumbent_contract_stated": _typed(
                row.get("incumbent_contract_number")),
            "value_range_stated": _typed(row.get("value_range")),
            "vehicle_stated": _typed(row.get("acquisition_strategy")),
            "anticipated_solicitation": _timing(
                row.get("rfp_fy"), row.get("rfp_quarter_or_date")),
            "anticipated_award": _timing(
                row.get("award_fy"), row.get("award_quarter")),
            "set_aside_stated": _set_aside(
                row.get("socioeconomic_designation")),
            "revised_from_prior_posting": revised,
            "_join_keys": {
                "incumbent_contract_number": _typed(
                    row.get("incumbent_contract_number")),
                "mission_organization": _typed(row.get("mission_organization")),
            },
        }
        pco = _typed(row.get("pco"))
        records.append(RawOpportunity(
            source=SOURCE_NAME,
            source_id=_source_id(row),
            title=title,
            agency=AGENCY,
            set_aside=normalized["set_aside_stated"],
            estimated_value=_lower_bound(row.get("value_range")),
            api_url=PDF_URL,
            contacts=([OpportunityContact(
                name=pco, title="Procuring Contracting Officer",
                contact_type="primary",
            )] if pco else []),
            raw_payload={
                "_normalized": normalized,
                "_citation_url": PDF_URL,
                "_evidence_tier": "PROGRAM",
            },
        ))
    return records, dropped


class NavyNawcadLrafSource(DataSource):
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
            return True, f"NAWCAD FY26 LRAF reachable ({len(rows)} parsed rows)"
        except Exception as exc:  # noqa: BLE001
            return False, f"NAWCAD FY26 LRAF unavailable: {exc}"

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        try:
            blob, _headers = (self._fetch_bytes or fetch_pdf)(PDF_URL)
            rows = _rows_from_pdf(blob)
        except Exception as exc:  # noqa: BLE001
            self.last_provenance = ProvenanceEnvelope(
                source=self.name, status="failed", mode=MODE,
                retrieval_mode="live", retrieved_at=datetime.now(timezone.utc),
                data_as_of=DATA_AS_OF, record_count=0,
                limitations=[f"forecast retrieval failed: {exc}"],
                retrieval_url=PDF_URL,
            ).model_dump(mode="json")
            LOG.warning("NAWCAD LRAF retrieval failed: %s", exc)
            return []

        matched, dropped = _normalize_rows(rows, query)
        capped = cap_disclosed(matched, query.limit or len(matched), key="items")
        records = capped["items"]
        limitations = [
            "NAWCAD LRAF rows are planning intent, not solicitations; acquisition strategy and timing may change",
            "Asterisks identify source revisions; gray action-occurred shading is not inferred from extracted text",
            "The source does not publish NAICS or PSC fields",
        ]
        if dropped:
            limitations.append(f"dropped {dropped} malformed or untitled rows")
        if capped.get("truncated"):
            limitations.append(str(capped["truncated_note"]))
        self.last_provenance = ProvenanceEnvelope(
            source=self.name, status="partial" if dropped else "complete",
            mode=MODE, retrieval_mode="live",
            retrieved_at=datetime.now(timezone.utc), data_as_of=DATA_AS_OF,
            record_count=len(records), limitations=limitations,
            public_detail="Live official NAWCAD FY26 Long-Range Acquisition Forecast PDF",
            retrieval_url=PDF_URL,
            raw_content_sha256=hashlib.sha256(blob).hexdigest(),
            raw_content_kind="application/pdf",
            normalization_version="navy_nawcad_lraf.v1.2026-08-08",
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
    register_source(NavyNawcadLrafSource)
except ValueError:
    pass
