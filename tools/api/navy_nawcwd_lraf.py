"""NAWCWD FY26 Long-Range Acquisition Forecast (official PDF).

The 30-column Weapons Division file carries incumbent contract numbers,
solicitation numbers, NAICS/PSC, acquisition strategy, value bands, timing,
clearances, office codes, and named POCs.  Rows are agency PROGRAM intent;
they are never solicitation records and therefore keep action dates empty.
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
from tools.api.pdf_forecast import (
    clean, fetch_pdf, pdf_tables, stated,
)
from tools.api.provenance import ProvenanceEnvelope
from tools.text_match import matching_phrases


LOG = logging.getLogger(__name__)
SOURCE_NAME = "navy_nawcwd_lraf"
AGENCY = "Department of the Navy / NAWCWD"
PDF_URL = (
    "https://www.navair.navy.mil/nawcwd/sites/g/files/jejdrs601/files/"
    "document/%5Bfilename%5D/NAWCWD%20LRAF%20FY%202026.pdf"
)
MODE = "live_nawcwd_fy26_lraf_pdf"
DATA_AS_OF = "2026-01-13"

_COLUMNS = (
    "row", "title", "description", "group_name", "requiring_org_code",
    "value_range", "procurement_method", "contract_type",
    "procurement_instrument", "solicitation_number", "solicitation_fy",
    "solicitation_quarter", "award_fy", "award_quarter", "pop_months",
    "follow_on_or_new", "existing_contract_number", "incumbent_contractor",
    "place_of_performance", "naics", "psc", "facility_clearance",
    "personnel_clearance", "procurement_office", "contracting_poc_name",
    "contracting_poc_email", "secondary_poc_name", "secondary_poc_email",
    "procurement_office_code", "comments",
)
_NAICS = re.compile(r"(?<!\d)\d{6}(?!\d)")
_PSC = re.compile(r"\b[A-Z0-9]{4}\b", re.I)
_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_MONEY = re.compile(
    r"(?:>|over|above|at least)?\s*\$\s*(\d[\d,]*(?:\.\d+)?)\s*"
    r"(K|M|B|thousand|million|billion)\b", re.I,
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
            if not clean(values[0]).isdigit():
                continue
            rows.append({key: clean(value)
                         for key, value in zip(_COLUMNS, values)})
    if not rows:
        raise ValueError("NAWCWD PDF has no recognizable 30-column LRAF rows")
    return rows


def _not_placeholder(value: Any) -> Optional[str]:
    return stated(value)


def _source_id(row: dict[str, str]) -> str:
    title = clean(row.get("title"))
    title_hash = hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()[:10]
    solicitation = _not_placeholder(row.get("solicitation_number"))
    if solicitation:
        key = re.sub(r"[^A-Z0-9]+", "", solicitation.upper())
        return f"nawcwd-lraf-solicitation-{key}"
    incumbent = _not_placeholder(row.get("existing_contract_number"))
    if incumbent:
        key = re.sub(r"[^A-Z0-9]+", "", incumbent.upper())
        return f"nawcwd-lraf-incumbent-{key}-{title_hash}"
    basis = "\x1f".join((title, clean(row.get("requiring_org_code")),
                         clean(row.get("solicitation_fy")),
                         clean(row.get("solicitation_quarter"))))
    return "nawcwd-lraf-composite-" + hashlib.sha256(
        basis.casefold().encode("utf-8")).hexdigest()[:20]


def _lower_bound(value: Any) -> Optional[float]:
    text = clean(value)
    if not text or text.lstrip().startswith("<"):
        return None
    match = _MONEY.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", "")) * _MULTIPLIER[
        match.group(2).casefold()]


def _set_aside(value: Any) -> Optional[str]:
    method = _not_placeholder(value)
    if not method:
        return None
    lowered = method.casefold()
    if "set-aside" in lowered or "set aside" in lowered:
        return method
    return None


def _agency_allowed(query: SourceQuery) -> bool:
    if not query.agencies:
        return True
    wanted = " ".join(query.agencies).casefold()
    return any(token in wanted for token in (
        "navy", "nawcwd", "weapons division", "department of defense", "dod",
    ))


def _matches(row: dict[str, str], query: SourceQuery) -> bool:
    if not _agency_allowed(query):
        return False
    haystack = " ".join(row.values())
    if query.keywords and not matching_phrases(haystack, query.keywords):
        return False
    naics = _not_placeholder(row.get("naics")) or ""
    if query.naics_codes and not any(
            naics.startswith(str(code)) for code in query.naics_codes):
        return False
    psc = (_not_placeholder(row.get("psc")) or "").upper()
    if query.psc_codes and not any(
            psc.startswith(str(code).upper()) for code in query.psc_codes):
        return False
    if query.set_asides:
        method = clean(row.get("procurement_method")).casefold()
        if not any(clean(value).casefold() in method
                   for value in query.set_asides):
            return False
    return True


def _contacts(row: dict[str, str]) -> list[OpportunityContact]:
    contacts: list[OpportunityContact] = []
    for name_key, email_key, contact_type in (
        ("contracting_poc_name", "contracting_poc_email", "primary"),
        ("secondary_poc_name", "secondary_poc_email", "secondary"),
    ):
        names = [clean(value) for value in
                 re.split(r"\n|(?<=\w)\s{2,}", str(row.get(name_key) or ""))
                 if clean(value)]
        emails = _EMAIL.findall(str(row.get(email_key) or ""))
        for index, email in enumerate(emails):
            contacts.append(OpportunityContact(
                name=names[index] if index < len(names) else (
                    clean(row.get(name_key)) or None),
                email=email, contact_type=contact_type,
            ))
    return contacts


def _normalize_rows(
    rows: list[dict[str, str]], query: SourceQuery,
) -> tuple[list[RawOpportunity], int]:
    records: list[RawOpportunity] = []
    dropped = 0
    for row in rows:
        title = clean(row.get("title"))
        if not title:
            dropped += 1
            continue
        if not _matches(row, query):
            continue
        normalized = {key: clean(row.get(key)) for key in _COLUMNS}
        normalized["solicitation_number_stated"] = _not_placeholder(
            row.get("solicitation_number"))
        normalized["incumbent_contract_stated"] = _not_placeholder(
            row.get("existing_contract_number"))
        normalized["incumbent_stated"] = _not_placeholder(
            row.get("incumbent_contractor"))
        normalized["_join_keys"] = {
            "solicitation_number": normalized["solicitation_number_stated"],
            "incumbent_contract_number": normalized["incumbent_contract_stated"],
            "requiring_org_code": _not_placeholder(row.get("requiring_org_code")),
            "naics": next(iter(_NAICS.findall(clean(row.get("naics")))), None),
            "psc": next(iter(_PSC.findall(clean(row.get("psc")))), None),
        }
        records.append(RawOpportunity(
            source=SOURCE_NAME,
            source_id=_source_id(row),
            title=title,
            agency=AGENCY,
            naics_code=normalized["_join_keys"]["naics"],
            psc_code=normalized["_join_keys"]["psc"],
            set_aside=_set_aside(row.get("procurement_method")),
            estimated_value=_lower_bound(row.get("value_range")),
            api_url=PDF_URL,
            contacts=_contacts(row),
            raw_payload={
                "_normalized": normalized,
                "_citation_url": PDF_URL,
                "_evidence_tier": "PROGRAM",
            },
        ))
    return records, dropped


class NavyNawcwdLrafSource(DataSource):
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
            return True, f"NAWCWD FY26 LRAF reachable ({len(rows)} parsed rows)"
        except Exception as exc:  # noqa: BLE001
            return False, f"NAWCWD FY26 LRAF unavailable: {exc}"

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
            LOG.warning("NAWCWD LRAF retrieval failed: %s", exc)
            return []

        matched, dropped = _normalize_rows(rows, query)
        capped = cap_disclosed(matched, query.limit or len(matched), key="items")
        records = capped["items"]
        limitations = [
            "NAWCWD LRAF rows are planning estimates, not solicitations; values, dates, strategies, and POCs may change",
            "Source row numbers are retained for traceability but never used alone as stable identifiers",
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
            public_detail="Live official NAWCWD FY26 Long-Range Acquisition Forecast PDF",
            retrieval_url=PDF_URL,
            raw_content_sha256=hashlib.sha256(blob).hexdigest(),
            raw_content_kind="application/pdf",
            normalization_version="navy_nawcwd_lraf.v1.2026-08-08",
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
    register_source(NavyNawcwdLrafSource)
except ValueError:
    pass
