"""HHS OIG List of Excluded Individuals/Entities (monthly full CSV).

LEIE name hits are review-grade risk signals, not an automatic procurement
disqualification.  The public bulk file omits SSNs/EINs, and this adapter also
discards DOB and street address so stored assessment evidence does not become a
second personal-data corpus.  Identity confirmation belongs in OIG's online
verification workflow.
"""

from __future__ import annotations

import csv
import hashlib
import io
from typing import Any, Optional

from tools.api._accepted_wave import (FetchBytes, fetch_bytes, payload,
                                      receipt)
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source


SOURCE_NAME = "hhs_oig_leie"
MODE = "live_hhs_oig_leie_monthly_csv"
CSV_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
LANDING_URL = (
    "https://oig.hhs.gov/exclusions/leie-database-supplement-downloads/"
)
EXPECTED_FIELDS = {
    "LASTNAME", "FIRSTNAME", "BUSNAME", "GENERAL", "SPECIALTY", "NPI",
    "EXCLTYPE", "EXCLDATE", "REINDATE", "WAIVERDATE", "WVRSTATE",
}


def _text(value: Any) -> Optional[str]:
    text = " ".join(str(value or "").split())
    return text or None


def _day(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) != 8 or text == "00000000" or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _identifier(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text or set(text) <= {"0"}:
        return None
    return text


def _names(row: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    business = _text(row.get("BUSNAME"))
    person = _text(" ".join(filter(None, (
        _text(row.get("FIRSTNAME")), _text(row.get("MIDNAME")),
        _text(row.get("LASTNAME")),
    ))))
    return business, person


def _stable_id(row: dict[str, Any]) -> str:
    business, person = _names(row)
    basis = "|".join(str(value or "").casefold() for value in (
        business, person, row.get("NPI"), row.get("UPIN"), row.get("CITY"),
        row.get("STATE"), row.get("EXCLTYPE"), row.get("EXCLDATE"),
    ))
    return "leie:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _matches(row: dict[str, Any], terms: list[str]) -> list[str]:
    business, person = _names(row)
    haystack = " ".join(filter(None, (business, person))).casefold()
    return [term for term in terms if term in haystack]


def _normalize(row: dict[str, Any], matched: list[str]) -> Optional[dict[str, Any]]:
    business, person = _names(row)
    if not business and not person:
        return None
    is_person = not bool(business)
    return {
        "record_id": _stable_id(row),
        "subject_type": "individual" if is_person else "entity",
        "business_name": business,
        "person_name": person if is_person else None,
        "general_classification": _text(row.get("GENERAL")),
        "specialty": _text(row.get("SPECIALTY")),
        "upin": _identifier(row.get("UPIN")),
        "npi": _identifier(row.get("NPI")),
        "city": _text(row.get("CITY")),
        "state": _text(row.get("STATE")),
        "zip": _text(row.get("ZIP")),
        "exclusion_type": _text(row.get("EXCLTYPE")),
        "exclusion_date": _day(row.get("EXCLDATE")),
        "reinstatement_date": _day(row.get("REINDATE")),
        "waiver_date": _day(row.get("WAIVERDATE")),
        "waiver_state": _text(row.get("WVRSTATE")),
        "matched_terms": matched,
        "_join_keys": {
            "npi": _identifier(row.get("NPI")),
            "upin": _identifier(row.get("UPIN")),
            "entity_name": business,
            "person_name": person if is_person else None,
        },
        "screening_disposition": "candidate match; manual identity verification required",
        "semantics": (
            "current HHS program exclusion risk; not automatic SAM suspension, "
            "debarment, or procurement disqualification"
        ),
        "citation_url": LANDING_URL,
    }


def _parse_csv(blob: bytes, query: SourceQuery) -> tuple[list[dict[str, Any]], int]:
    text = blob.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    fields = {str(value or "").strip().upper() for value in reader.fieldnames or []}
    if not EXPECTED_FIELDS.issubset(fields):
        raise ValueError(
            "LEIE CSV header missing " + ", ".join(sorted(EXPECTED_FIELDS - fields))
        )
    terms = [" ".join(str(value).strip().strip('"').casefold().split())
             for value in query.keywords if str(value).strip()]
    items: list[dict[str, Any]] = []
    dropped = 0
    for native in reader:
        matched = _matches(native, terms)
        if not matched:
            continue
        row = _normalize(native, matched)
        if row is None:
            dropped += 1
        else:
            items.append(row)
    return items, dropped


class HhsOigLeieSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetcher: Optional[FetchBytes] = None) -> None:
        self._fetcher = fetcher or fetch_bytes

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use screen()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            blob, _headers = self._fetcher(CSV_URL, timeout=30.0, retries=1)
            header = blob[:300].decode("utf-8-sig", errors="replace").upper()
            if "LASTNAME,FIRSTNAME" in header and "EXCLTYPE" in header:
                return True, "official current LEIE CSV reachable"
            return False, "LEIE endpoint returned an unexpected CSV header"
        except Exception as exc:  # noqa: BLE001
            return False, f"LEIE CSV unavailable: {exc}"

    def screen(self, query: SourceQuery) -> dict[str, Any]:
        if not query.keywords:
            return payload(
                source=self.name, mode=MODE, items=[], status="partial",
                limitations=[
                    "unscoped full-file screening refused; pass entity or person names as keywords",
                ],
            )
        try:
            blob, headers = self._fetcher(CSV_URL, timeout=90.0, retries=2)
            matched, dropped = _parse_csv(blob, query)
        except Exception as exc:  # noqa: BLE001
            return payload(
                source=self.name, mode=MODE, items=[], status="failed",
                limitations=[f"LEIE retrieval or parse failed: {exc}"],
            )
        limit = max(1, min(query.limit, 100))
        total = len(matched)
        items = matched[:limit]
        limitations = [
            "name matching is candidate screening only; every hit requires manual identity verification",
            "LEIE is not equivalent to SAM suspension/debarment and never auto-disqualifies a party",
            "DOB and street address are discarded; the bulk file contains no SSN or EIN",
            "the server ignores Range requests, so a scoped screen still downloads the monthly full CSV",
        ]
        if dropped:
            limitations.append(f"ignored {dropped} malformed matched rows")
        if total > limit:
            limitations.append(f"kept first {limit} of {total} candidate matches")
        source_receipt = receipt(
            source=self.name, url=CSV_URL,
            query={"keywords": query.keywords, "limit": query.limit},
            blob=blob, normalized=items, headers=headers,
            content_kind="text/csv",
        )
        return payload(
            source=self.name, mode=MODE, items=items,
            status="partial" if dropped or total > limit else "complete",
            limitations=limitations, source_receipt=source_receipt,
            total_matched=total,
            public_detail="Official current HHS OIG LEIE monthly CSV",
        )


try:
    register_source(HhsOigLeieSource)
except ValueError:
    pass
