"""GSA eLibrary MAS contractor extract - per-SIN vehicle holders (keyless).

Vehicle-path evidence no award-derived source carries: which vendors HOLD a
Multiple Award Schedule contract under a given SIN (e.g. 511210 IT Software,
54151S IT Services, 54151HACS cyber), including holders with zero reported
orders, who are invisible to FPDS/USAspending. Each row carries the SAM UEI
(joins the SAM entity lane), the MAS contract number (the IDV PIID that shows
up as referenced-IDV parent on schedule orders), the closed-for-new-award
flag, both contract end dates (vehicle-expiry timing), and vendor email/phone
(teaming outreach).

Feed:   https://gsaelibrary.gsa.gov/elib_contracts/schedule_MAS.csv
Browse: https://www.gsaelibrary.gsa.gov/ElibMain/scheduleSummary.do?scheduleNumber=MAS

Feed realities (verified live 2026-08-08): ~30MB point-in-time snapshot, no
archive or deltas; anonymous GET, Range honored (healthcheck probes 2KB);
header row contains embedded newlines and two trailing blank columns, so
parsing is header-name driven through the csv module, never positional
splitting. One row per (vendor, SIN); holders aggregate across rows.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from tools.api._http import get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.api.provenance import ProvenanceEnvelope

CSV_URL = "https://gsaelibrary.gsa.gov/elib_contracts/schedule_MAS.csv"
BROWSE_URL = ("https://www.gsaelibrary.gsa.gov/ElibMain/scheduleSummary.do"
              "?scheduleNumber=MAS")
#: per-holder citation: the eLibrary contractor detail page for that PIID
CONTRACTOR_URL = ("https://www.gsaelibrary.gsa.gov/ElibMain/contractorInfo.do"
                  "?contractNumber={contract}&contractorName={vendor}"
                  "&executeQuery=YES")
HOLDER_CAP = 200
FETCH_TIMEOUT = 180.0  # ~30MB pull

#: MAS SINs are NAICS-rooted, digit-led alphanumerics (238160, 511210,
#: 54151S, 54151HACS). A keyword of this shape filters the SIN column;
#: any other keyword filters the vendor-name column.
_SIN_SHAPE = re.compile(r"^[0-9]{3,6}[0-9A-Z]{0,6}$")

#: header names after whitespace normalization (the raw header embeds
#: newlines inside several column names)
_COLUMNS = {
    "sin": "Category",
    "vendor": "Vendor",
    "contract_number": "Contract #",
    "closed": "Closed for New Award",
    "phone": "Phone",
    "email": "Email",
    "option_end": "Current Option Period End Date",
    "ultimate_end": "Ultimate Contract End Date",
    "uei": "SAM UEI",
}

_SNAPSHOT_LIMIT = ("point-in-time snapshot; the file states no as-of date "
                   "and GSA publishes no archive or deltas")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _envelope(status: str, record_count: int,
              limitations: list) -> dict:
    return ProvenanceEnvelope(
        source="gsa_elibrary",
        status=status,
        mode="live_bulk_csv",
        retrieval_mode="live",
        retrieved_at=_utc_now(),
        record_count=record_count,
        limitations=limitations,
    ).model_dump(mode="json")


def _norm_header(name: str) -> str:
    return " ".join((name or "").split())


def _iso_date(value: str) -> Optional[str]:
    """'Jul 31, 2029' -> '2029-07-31'; unparseable text passes through
    verbatim (the source's own words beat a dropped field)."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%b %d, %Y").date().isoformat()
    except ValueError:
        return text


def _closed_flag(value: str) -> bool:
    """Blank means open for new awards; any marker other than an explicit
    'N'/'No' means the vehicle is closed to new offers."""
    return (value or "").strip().lower() not in {"", "n", "no", "none"}


def _split_terms(query: SourceQuery) -> "tuple[list, list]":
    """(sin_terms, vendor_terms): SIN-shaped keywords and every NAICS code
    filter the SIN column; the rest filter vendor names."""
    sin_terms: list = []
    vendor_terms: list = []
    for kw in (query.keywords or []):
        text = (kw or "").strip().strip('"')
        if not text:
            continue
        if _SIN_SHAPE.match(text.upper()):
            sin_terms.append(text.upper())
        else:
            vendor_terms.append(text.lower())
    for code in (query.naics_codes or []):
        text = (code or "").strip().upper()
        if text and _SIN_SHAPE.match(text) and text not in sin_terms:
            sin_terms.append(text)
    return sin_terms, vendor_terms


class GsaElibrarySource(DataSource):
    name = "gsa_elibrary"
    kind = SourceKind.ENRICHMENT

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError(
            "enrichment-only source; use vehicle_holders()")

    def healthcheck(self) -> "tuple[bool, str]":
        """2KB Range probe (server honors Ranges; verified 206) - never the
        30MB pull. Green requires the real header, not just an HTTP 200."""
        try:
            head = get_text(CSV_URL, headers={"Range": "bytes=0-2047"},
                            timeout=10.0, retries=1)
        except Exception as e:  # noqa: BLE001
            return False, "MAS extract unreachable: {}".format(e)
        if "Contract #" in head and "SAM UEI" in head:
            return True, ("MAS extract reachable (Range probe, "
                          "header verified)")
        return False, ("MAS extract responded without the expected header; "
                       "column layout may have drifted")

    # ------------------------------------------------------------------ #
    # enrichment method consumed by engines
    # ------------------------------------------------------------------ #
    def vehicle_holders(self, query: SourceQuery) -> "dict[str, Any]":
        """MAS vehicle holders matching the query, aggregated per contract.

        Keywords hit vendor names; SIN-shaped keywords and NAICS codes hit
        the SIN column (exact or prefix, so '54151' covers the family). A
        holder's ``sins`` lists the contract's FULL witnessed holdings, not
        only the matched rows. Payload: ``holders`` capped at 200 with the
        cap disclosed, plus scan/drop counts and provenance.
        """
        sin_terms, vendor_terms = _split_terms(query)
        if not sin_terms and not vendor_terms:
            return {
                "holders": [], "total_matched": 0,
                "source_url": CSV_URL, "browse_url": BROWSE_URL,
                "_provenance": _envelope("partial", 0, [
                    "unscoped query refused: the extract is a 30MB bulk "
                    "file; pass vendor keywords, SIN codes, or NAICS codes",
                ]),
            }

        try:
            text = get_text(CSV_URL, timeout=FETCH_TIMEOUT, retries=2)
        except Exception as e:  # noqa: BLE001
            return {
                "holders": [], "total_matched": 0, "error": str(e),
                "source_url": CSV_URL, "browse_url": BROWSE_URL,
                "_provenance": _envelope("failed", 0, [_SNAPSHOT_LIMIT]),
            }

        reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
        try:
            header = next(reader)
        except StopIteration:
            header = []
        index = {}
        for pos, name in enumerate(header):
            normalized = _norm_header(name)
            if normalized and normalized not in index:
                index[normalized] = pos
        missing = [c for c in _COLUMNS.values() if c not in index]
        if missing:
            return {
                "holders": [], "total_matched": 0,
                "error": "column layout drifted; missing: {}".format(
                    ", ".join(missing)),
                "source_url": CSV_URL, "browse_url": BROWSE_URL,
                "_provenance": _envelope("failed", 0, [_SNAPSHOT_LIMIT]),
            }
        col = {key: index[name] for key, name in _COLUMNS.items()}
        width = max(col.values()) + 1

        rows_scanned = 0
        rows_dropped = 0
        sins_by_contract: "dict[str, list]" = {}
        matched: "dict[str, dict]" = {}  # contract -> holder, arrival order

        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            rows_scanned += 1
            if len(row) < width:
                rows_dropped += 1
                continue
            vendor = row[col["vendor"]].strip()
            contract = row[col["contract_number"]].strip()
            sin = row[col["sin"]].strip().upper()
            if not vendor or not contract:
                rows_dropped += 1
                continue

            if sin:
                holdings = sins_by_contract.setdefault(contract, [])
                if sin not in holdings:
                    holdings.append(sin)

            vendor_hits = [t for t in vendor_terms if t in vendor.lower()]
            sin_hits = [t for t in sin_terms
                        if sin == t or sin.startswith(t)]
            if not vendor_hits and not sin_hits:
                continue

            holder = matched.get(contract)
            if holder is None:
                holder = {
                    "vendor": vendor,
                    "uei": row[col["uei"]].strip() or None,
                    "contract_number": contract,
                    "sins": sins_by_contract.setdefault(contract, []),
                    "option_end": _iso_date(row[col["option_end"]]),
                    "ultimate_end": _iso_date(row[col["ultimate_end"]]),
                    "closed_for_new_award": _closed_flag(row[col["closed"]]),
                    "email": row[col["email"]].strip() or None,
                    "phone": row[col["phone"]].strip() or None,
                    "matched": [],
                    "url": CONTRACTOR_URL.format(
                        contract=quote(contract), vendor=quote(vendor)),
                }
                matched[contract] = holder
            for term in vendor_hits + sin_hits:
                if term not in holder["matched"]:
                    holder["matched"].append(term)

        # A NAMED FIRM OUTRANKS A BROAD SIN MATCH. The cap is a real bound
        # on a 30MB file, but truncating in arrival order truncates
        # ALPHABETICALLY: measured 2026-08-08, a caller asked for ten named
        # rivals plus capability terms and got "1901 GROUP", "3AM
        # INNOVATIONS", "4CLICKS" while ALTANA, RESILINC and SPHERA fell
        # past row 200. The lane then reported zero rivals holding a
        # vehicle, which was false. When a caller names a vendor, that row
        # is the answer; SIN breadth is the backdrop.
        def _rank(holder: dict) -> tuple:
            """A NAMED FIRM FIRST, then SIN breadth, then alphabetical.

            Every non-SIN keyword becomes a vendor term, so a capability
            phrase and a company name arrive indistinguishable. What tells
            them apart is WHERE they match: a caller naming "Altana" matches
            the START of "ALTANA TECHNOLOGIES USG INC", while a capability
            phrase only ever matches incidentally mid-string. Prefix
            matches are the caller's named firms.
            """
            vendor = (holder.get("vendor") or "").casefold()
            named = any(vendor.startswith(t) for t in holder["matched"]
                        if t in vendor_terms)
            broad = any(t in vendor_terms for t in holder["matched"])
            return (0 if named else (1 if broad else 2), vendor)

        holders = sorted(matched.values(), key=_rank)
        payload = cap_disclosed(holders, HOLDER_CAP, key="holders")
        limitations = [_SNAPSHOT_LIMIT]
        if rows_dropped:
            limitations.append(
                "{} malformed rows dropped of {} scanned".format(
                    rows_dropped, rows_scanned))
        payload.update({
            "rows_scanned": rows_scanned,
            "rows_dropped": rows_dropped,
            "source_url": CSV_URL,
            "browse_url": BROWSE_URL,
            "_provenance": _envelope(
                "partial" if rows_dropped else "complete",
                len(payload["holders"]), limitations),
        })
        return payload


# The @register_source path requires a SourceSpec row in
# tools/api/source_catalog.py, which the integrator wires together with the
# registrar import. Guarded so the adapter is importable and testable before
# that row lands; once cataloged, this registers exactly like the decorator.
try:
    register_source(GsaElibrarySource)
except ValueError:
    pass
