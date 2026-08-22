"""HHS OSDBU Small Business Contract Opportunity Forecast adapter.

The public SBCX application exposes its published forecast records through an
anonymous JSON endpoint.  Measured 2026-08-18: the ``?filter=`` parameter
matches title, NAICS, and operating division only, so description-borne
signal can never return from term probes; the SAME endpoint with an EMPTY
filter returns the complete published census in one free request.  The
census is therefore the ONE ingest path (the bounded probe path was killed
the same day; two ingest paths into one store is a future agent's
confusion).  Every returned row is still checked locally (published-only,
historical, implausible-timing).  The adapter emits
:class:`ForecastRecord` only.
``search()`` deliberately returns an empty list so an HHS forecast can never be
promoted into the active-solicitation flow.

Official surfaces:

* API: https://osdbu.hhs.gov/api/sbcxopportunities?filter=  (empty filter = full census)
* UI:  https://osdbu.hhs.gov/industry/opportunity-forecast

The public response does not state a dataset effective date.  ``data_as_of``
therefore remains unset; retrieval time is recorded separately in provenance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

from agents.schemas import ForecastRecord, RawOpportunity
from tools.api import _http
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.provenance import ProvenanceEnvelope
from tools.toggles import is_enabled

LOG = logging.getLogger(__name__)

SOURCE_NAME = "hhs_sbcx"
API_URL = "https://osdbu.hhs.gov/api/sbcxopportunities"
APP_FORECAST_URL = "https://osdbu.hhs.gov/industry/opportunity-forecast"
HEADERS = {
    "User-Agent": "GTM-Group-LILA/1.0 (public federal forecast research)",
    "Accept": "application/json",
}

HEALTHCHECK_TERM = "public"
MAX_TOTAL_RECORDS = int(
    os.environ.get("LILA_HHS_SBCX_MAX_TOTAL_RECORDS", "15000")
)
CENSUS_URL = API_URL + "?filter="

DIVISIONS = {
    "ACF": "Administration for Children and Families",
    "ACL": "Administration for Community Living",
    "AHRQ": "Agency for Healthcare Research and Quality",
    "ASPR": "Administration for Strategic Preparedness and Response",
    "CDC": "Centers for Disease Control and Prevention",
    "CMS": "Centers for Medicare & Medicaid Services",
    "FDA": "Food and Drug Administration",
    "HRSA": "Health Resources and Services Administration",
    "IHS": "Indian Health Service",
    "NIH": "National Institutes of Health",
    "OS": "Office of the Secretary",
    "SAMHSA": "Substance Abuse and Mental Health Services Administration",
}

_DEPARTMENT_ALIASES = (
    "hhs",
    "health and human services",
    "department of health",
)

RANGE_LABELS = {
    "RANGE_1": "> $0 and <= $10K",
    "RANGE_2": "> $10K and <= $25K",
    "RANGE_3": "> $25K and < $250K",
    "RANGE_4": ">= $250K and < $700K",
    "RANGE_5": ">= $700K and < $1.5M",
    "RANGE_6": ">= $1.5M and < $3M",
    "RANGE_7": ">= $3M and < $7M",
    "RANGE_8": ">= $7M and < $13M",
    "RANGE_9": ">= $13M and < $20M",
    "RANGE_10": ">= $20M and < $50M",
    "RANGE_11": ">= $50M and < $100M",
    "RANGE_12": ">= $100M",
    "RANGE_TBD": "TBD",
}

STRATEGY_LABELS = {
    "fullopen": "Full and Open",
    "smallBusiness": "Small Business Set-Aside",
    "tbd": "To be determined",
}

_PLACEHOLDERS = {"", "tbd", "n/a", "na", "none", "to be determined"}
_NAICS_RE = re.compile(r"^\d{6}$")


def probe_url(term: str) -> str:
    """Return the exact official API URL used for one free-text probe."""

    return API_URL + "?filter=" + quote(term, safe="")


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


def _stated(value: Any) -> Optional[str]:
    text = _text(value)
    if text is None or text.casefold() in _PLACEHOLDERS:
        return None
    return text


def _naics(rec: dict[str, Any]) -> Optional[str]:
    stated = _stated(rec.get("primaryNAICS"))
    return stated if stated and _NAICS_RE.fullmatch(stated) else None


def _month_stamp(year: Any, month: Any) -> Optional[str]:
    year_ok = isinstance(year, int) and not isinstance(year, bool) and year >= 1990
    month_ok = (
        isinstance(month, int)
        and not isinstance(month, bool)
        and 1 <= month <= 12
    )
    if year_ok and month_ok:
        return f"{year:04d}-{month:02d}"
    if year_ok:
        return str(year)
    return None


def _is_past_forecast(rec: dict[str, Any], *, as_of: datetime) -> bool:
    """Return true only when every stated planning date is already past.

    SBCX's public search includes historical rows that remain marked
    PUBLISHED.  They are valid source records but no longer forward forecast
    signals.  Undated rows remain eligible because absence of a date is not
    evidence that the requirement expired.
    """
    current = (as_of.year, as_of.month)
    stated: list[tuple[int, int]] = []
    for year_key, month_key in (
        ("targetSolicitationYear", "targetSolicitationMonth"),
        ("targetAwardYear", "targetAwardMonth"),
    ):
        year = rec.get(year_key)
        month = rec.get(month_key)
        if isinstance(year, int) and not isinstance(year, bool):
            month_value = (
                month if isinstance(month, int) and not isinstance(month, bool)
                and 1 <= month <= 12 else 12
            )
            stated.append((year, month_value))
    return bool(stated) and max(stated) < current


def _has_implausible_timing(rec: dict[str, Any], *, as_of: datetime) -> bool:
    """Reject obvious public-data entry errors, not merely distant plans."""
    ceiling = as_of.year + 10
    for key in ("targetSolicitationYear", "targetAwardYear"):
        year = rec.get(key)
        if isinstance(year, int) and not isinstance(year, bool):
            if year < 1990 or year > ceiling:
                return True
    return False


def _person(first: Any, last: Any) -> Optional[str]:
    parts = [part for part in (_stated(first), _stated(last)) if part]
    return " ".join(parts) or None


def map_record(
    rec: Any,
    *,
    retrieved_at: Optional[datetime] = None,
) -> Optional[ForecastRecord]:
    """Map one published SBCX row directly into the forecast-only schema."""

    if not isinstance(rec, dict):
        return None
    if (_text(rec.get("status")) or "").upper() != "PUBLISHED":
        return None
    uuid = _stated(rec.get("uuid"))
    title = _stated(rec.get("title"))
    if not uuid or not title:
        return None

    division = (_stated(rec.get("divisionAcronym")) or "").upper()
    division_name = DIVISIONS.get(division)
    range_code = _stated(rec.get("totalContractRange")) or ""
    strategy_code = _text(rec.get("anticipatedStrategy")) or ""
    if strategy_code == "smallBusiness":
        set_aside = STRATEGY_LABELS[strategy_code]
    elif strategy_code == "fullopen":
        set_aside = STRATEGY_LABELS[strategy_code]
    else:
        set_aside = None

    solicitation = _month_stamp(
        rec.get("targetSolicitationYear"), rec.get("targetSolicitationMonth")
    )
    award = _month_stamp(rec.get("targetAwardYear"), rec.get("targetAwardMonth"))
    fiscal_year = _text(rec.get("targetAwardYear")) or _text(
        rec.get("targetSolicitationYear")
    )
    return ForecastRecord(
        source=SOURCE_NAME,
        source_id="hhs-fcst-" + uuid,
        agency="Department of Health and Human Services",
        component=(
            f"{division_name} ({division})"
            if division_name and division
            else division_name or division or None
        ),
        title=title,
        description=_stated(rec.get("description")),
        naics_code=_naics(rec),
        estimated_value_range=RANGE_LABELS.get(range_code) or _stated(range_code),
        anticipated_solicitation=solicitation,
        anticipated_award=award,
        fiscal_year=fiscal_year,
        set_aside=set_aside,
        url=APP_FORECAST_URL,
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
        data_as_of=None,
        forecast_status="PUBLISHED",
        incumbent_stated=_stated(rec.get("incumbentContractorName")),
        predecessor_contract_id=_stated(rec.get("contractNumber")),
        source_fields=dict(rec),
    )


@register_source
class HhsSbcxSource(DataSource):
    """Bounded reader for HHS-published planning signals."""

    name = SOURCE_NAME
    kind = SourceKind.DISCOVERY
    forecast_agency = "Department of Health and Human Services"

    def __init__(self, fetch_json: Optional[Callable[..., Any]] = None) -> None:
        self.enabled = is_enabled(self.name, True)
        self.last_provenance: dict[str, Any] = {}
        self._fetch_json = fetch_json

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        """Forecasts never enter the active-opportunity discovery flow."""

        return []

    def healthcheck(self) -> tuple[bool, str]:
        if not self.enabled:
            return True, "explicitly disabled by LILA_ENABLE_HHS_SBCX"
        fetch = self._fetch_json or _http.get_json
        try:
            payload = fetch(
                probe_url(HEALTHCHECK_TERM),
                headers=HEADERS,
                timeout=8.0,
                retries=1,
            )
        except Exception as exc:  # noqa: BLE001 - health probes report only
            return False, f"HHS SBCX forecast store unreachable: {exc}"
        if not isinstance(payload, list):
            return False, (
                "HHS SBCX forecast store returned a non-list payload "
                "(public contract may have changed)"
            )
        if not payload:
            return False, "HHS SBCX forecast store returned an empty probe result"
        if any(not isinstance(row, dict) for row in payload):
            return False, "HHS SBCX forecast store returned non-object rows"
        if any(
            (_text(row.get("status")) or "").upper() != "PUBLISHED"
            for row in payload
        ):
            return False, (
                "HHS SBCX public probe included a row not marked PUBLISHED"
            )
        return True, (
            f"official HHS SBCX store reachable ({len(payload)} published "
            f"rows for probe {HEALTHCHECK_TERM!r})"
        )

    def _set_provenance(
        self,
        *,
        status: str,
        retrieved_at: datetime,
        records: list[ForecastRecord],
        attempts: list[dict[str, Any]],
        limitations: list[str],
        pull_receipt: list[dict[str, Any]],
        omitted_terms: Optional[list[str]] = None,
    ) -> None:
        envelope = ProvenanceEnvelope(
            source=SOURCE_NAME,
            status=status,
            mode="live_sbcx_full_census",
            retrieval_mode="live",
            fallback=False,
            stale=False,
            retrieved_at=retrieved_at,
            data_as_of=None,
            record_count=len(records),
            attempts=attempts,
            limitations=limitations,
            public_detail=(
                "Official HHS OSDBU SBCX published-forecast search "
                f"({len(records)} unique records)"
            ),
        ).model_dump(mode="json")
        envelope.update(
            {
                # Current change-store guard consumes this legacy-compatible
                # boolean while the canonical envelope carries status.
                "complete": status == "complete",
                "source_url": API_URL,
                "landing_url": APP_FORECAST_URL,
                "classification": "agency forecast; never an active notice",
                "omitted_terms": list(omitted_terms or []),
                "sha256": hashlib.sha256(
                    json.dumps(
                        pull_receipt,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
        self.last_provenance = envelope

    def forecasts(
        self,
        query: Optional[SourceQuery] = None,
    ) -> list[ForecastRecord]:
        if not self.enabled:
            self.last_provenance = {
                "source": SOURCE_NAME,
                "status": "failed",
                "mode": "not_run",
                "complete": False,
                "records": 0,
                "source_url": API_URL,
                "classification": "agency forecast; never an active notice",
            }
            return []

        # The query argument keeps the family signature for the sweep;
        # a census ignores it (matching narrows downstream, never at the
        # wire), and SourceQuery's default page size must not silently
        # shrink the census.
        fetch = self._fetch_json or _http.get_json
        retrieved_at = datetime.now(timezone.utc)
        attempts: list[dict[str, Any]] = []
        pull_receipt: list[dict[str, Any]] = []
        errors: list[str] = []

        def _fail(error: str) -> None:
            attempts.append({"source": SOURCE_NAME + ":census",
                             "status": "failed", "error": error})
            self._set_provenance(
                status="failed",
                retrieved_at=retrieved_at,
                records=[],
                attempts=attempts,
                limitations=[error],
                pull_receipt=[],
                omitted_terms=[],
            )
            LOG.warning("hhs_sbcx pull failed: %s", error)
            raise RuntimeError("HHS SBCX forecast pull failed: " + error)

        try:
            payload = fetch(
                CENSUS_URL,
                headers=HEADERS,
                timeout=60.0,
                retries=2,
            )
        except Exception as exc:  # noqa: BLE001 - the one census request
            _fail(f"census pull failed: {exc}")
        if not isinstance(payload, list):
            _fail("census returned a non-list payload "
                  "(public contract may have changed)")

        rows = [row for row in payload if isinstance(row, dict)]
        malformed_payload_rows = len(payload) - len(rows)
        if malformed_payload_rows:
            errors.append(
                f"census returned {malformed_payload_rows} non-object rows"
            )
        attempts.append({
            "source": SOURCE_NAME + ":census",
            "status": "success",
            "count": len(rows),
            "retrieved_at": retrieved_at,
        })
        pull_receipt.append({"census": True, "url": CENSUS_URL, "rows": rows})

        output: list[ForecastRecord] = []
        seen: set[str] = set()
        malformed = 0
        non_published = 0
        past_forecasts = 0
        implausible_timing = 0
        for row in rows:
            if (_text(row.get("status")) or "").upper() != "PUBLISHED":
                non_published += 1
                continue
            if _is_past_forecast(row, as_of=retrieved_at):
                past_forecasts += 1
                continue
            if _has_implausible_timing(row, as_of=retrieved_at):
                implausible_timing += 1
                continue
            mapped = map_record(row, retrieved_at=retrieved_at)
            if mapped is None:
                malformed += 1
                continue
            if mapped.source_id in seen:
                continue
            seen.add(mapped.source_id)
            output.append(mapped)

        limitations = [
            "Records are agency-stated acquisition forecasts, not active "
            "solicitations; target months may be revised or cancelled",
            "The public SBCX endpoint states no dataset effective date; "
            "data_as_of is intentionally unset",
            "Census pull: one empty-filter request over the complete "
            "published store (the filter parameter matches title, NAICS, "
            "and division only, measured 2026-08-18)",
        ]
        limitations.extend(errors)
        if malformed:
            limitations.append(
                f"dropped {malformed} published rows with no stable uuid or title"
            )
        if non_published:
            limitations.append(
                f"excluded {non_published} rows not marked PUBLISHED"
            )
        if past_forecasts:
            limitations.append(
                f"excluded {past_forecasts} published historical rows whose "
                "stated solicitation and award timing had both passed"
            )
        if implausible_timing:
            limitations.append(
                f"excluded {implausible_timing} rows with a stated planning "
                f"year outside 1990 through {retrieved_at.year + 10}"
            )

        cap = MAX_TOTAL_RECORDS
        if len(output) > cap:
            limitations.append(
                f"kept first {cap} of {len(output)} matched records in source order"
            )
            output = output[:cap]
            errors.append("result cap applied")

        status = (
            "partial"
            if (errors or malformed or non_published)
            else "complete"
        )
        self._set_provenance(
            status=status,
            retrieved_at=retrieved_at,
            records=output,
            attempts=attempts,
            limitations=limitations,
            pull_receipt=pull_receipt,
            omitted_terms=[],
        )
        return output
