"""Department of Education Procurement Forecast adapter (forecast-stage discovery).

ED is not on the GSA Acquisition Gateway; its statutorily required procurement
forecast (P.L. 100-656) ships as one xlsx on the ED OSDBU "Forecast of ED
Contract Opportunities" page. The workbook carries fields no other pipeline
source has for ED before a solicitation exists: 'Tracking No.' (FY26-AP-nnnn,
a stable pre-award id usable for revision-to-revision diffing within the FY),
'Incumbent Contractor Name' (181 of 370 rows on the July 23, 2026 revision),
'Type of Competition' (the file's set-aside field: 8(a) and WOSB rows),
'Estimated Value of Contract $ Range', 'Target Award Quarter', and named
contracting POC e-mails (40 distinct @ed.gov addresses). That is recompete
targeting with named incumbents months before anything posts to SAM.gov.

Landing page: https://www.ed.gov/about/doing-business-ed/contract-opportunities/forecast-of-ed-contract-opportunities
Workbook:     linked from the landing page with a dated filename slug
              (us-department-of-education-procurement-forecast-july-23-2026-
              114098.xlsx, verified 2026-08-08). Every revision mints a NEW
              url, so each pull re-discovers the current link from the
              landing page instead of pinning a workbook address.

Operational facts, verified live 2026-08-08:
- ed.gov serves the landing page and the workbook to anonymous clients
  (HTTP 200, no browser User-Agent required, unlike state.gov's WAF).
- The first worksheet ('FY2026') opens with two preamble rows before the
  header: an 'as of <date>' banner (the source's own data_as_of) and a
  'Dept. Funding Code: 9100' line (joins to ED treasury symbol 91). It
  closes with a disclaimer row and a note row that live in the first column
  only; both are file furniture, skipped without counting as data loss.
- The trailing note states solicitations generally issue about one quarter
  before the anticipated award date, which is why 'Target Award Quarter'
  rides in raw_payload as the file's only solicitation-timing signal.
- Forecast rows are stated agency intent, never live deals: posted_date and
  response_deadline stay None.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional
from urllib.parse import urljoin

from openpyxl import load_workbook

from agents.schemas import OpportunityContact, RawOpportunity
from tools.api import _http
from tools.api.base import (
    DataSource,
    SourceKind,
    SourceQuery,
    cap_disclosed,
    register_source,
)
from tools.api.provenance import ProvenanceEnvelope
from tools.text_match import matching_phrases

LOG = logging.getLogger(__name__)

SOURCE_NAME = "ed_forecast"
AGENCY = "Department of Education"
ID_PREFIX = "ed-fcst-"
LANDING_URL = (
    "https://www.ed.gov/about/doing-business-ed/"
    "contract-opportunities/forecast-of-ed-contract-opportunities"
)

_XLSX_HREF_RE = re.compile(
    r"""href=["']([^"']*procurement[-_ ]forecast[^"']*\.xlsx)["']""",
    re.IGNORECASE,
)
_NAICS_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
#: Tracking numbers observed in the file: FY26-AP-4186 (370/373 rows conform;
#: the other three are a blank row and two first-column note rows).
_TRACKING_RE = re.compile(r"^FY\d{2}-[A-Z]{1,4}-\d+$", re.IGNORECASE)
_MONEY_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*([KMB])?", re.IGNORECASE)
_MULT = {"K": 1e3, "M": 1e6, "B": 1e9}
_AS_OF_RE = re.compile(r"as of\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE)
_FY_RE = re.compile(r"\bFY\s*(\d{4})\b", re.IGNORECASE)
_FUNDING_CODE_RE = re.compile(r"funding code\s*:?\s*(\d+)", re.IGNORECASE)

#: Placeholder spellings treated as absent in typed fields. 'TBD' is the only
#: one witnessed in the July 23, 2026 workbook; the rest guard revisions. The
#: verbatim cell still rides in raw_payload for traceability.
_PLACEHOLDERS = {
    "", "tbd", "none", "n/a", "na", "not applicable", "to be determined",
}

#: Verbatim workbook headers (canonicalized) to raw_payload keys. Both the
#: parenthetical NAICS header shipped in the real file and a bare spelling
#: are mapped so a trimmed future revision keeps parsing.
_HEADER_KEYS = {
    "tracking no.": "tracking_no",
    "tracking no": "tracking_no",
    "funding office": "funding_office",
    "contracting office": "contracting_office",
    "requirement type": "requirement_type",
    "contract name": "contract_name",
    "primary naics code (subject to change)": "naics_raw",
    "primary naics code": "naics_raw",
    "primary naics code description": "naics_description",
    "type of competition": "type_of_competition",
    "estimated value of contract $ range": "estimated_value_range",
    "estimated current fiscal year $ range": "estimated_current_fy_range",
    "incumbent contractor name": "incumbent_contractor_name",
    "point of contact name": "poc_name",
    "point of contact e-mail": "poc_email",
    "point of contact email": "poc_email",
    "target award quarter": "target_award_quarter",
}

#: How many leading rows may precede the header row (two witnessed).
_HEADER_SCAN_ROWS = 12


# --------------------------------------------------------------------------- #
# Fetch layer
# --------------------------------------------------------------------------- #
def _get_bytes(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: float = 60.0,
    retries: int = 3,
) -> "tuple[bytes, dict]":
    """GET raw bytes plus response headers, with _http's bounded-retry shape.

    _http exposes json/text helpers only, and an xlsx is a zip container
    that text decoding would corrupt. Rather than import httpx here, reuse
    _http's configured client factory (IPv4 pin, redirects, timeout) inside
    the same 0.5s/1s/2s backoff loop get_text uses.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _http._client(timeout) as client:
                resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.content, dict(resp.headers)
        except Exception as exc:  # noqa: BLE001 - transport error family varies
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def discover_workbook_url(html: str) -> Optional[str]:
    """First procurement-forecast .xlsx link on the landing page, absolute.

    The real href is site-relative (/media/document/...), so urljoin against
    the landing page resolves it.
    """
    match = _XLSX_HREF_RE.search(html or "")
    if not match:
        return None
    return urljoin(LANDING_URL, match.group(1))


# --------------------------------------------------------------------------- #
# Parse layer
# --------------------------------------------------------------------------- #
def _canon_header(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).lower()


def _fallback_key(canon: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", canon).strip("_")


def _clean_cell(value: Any) -> Any:
    """Collapse whitespace/nbsp in strings; empty strings become None."""
    if isinstance(value, str):
        text = " ".join(value.replace("\xa0", " ").split())
        return text or None
    return value


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = _clean_cell(value)
    if cleaned is None:
        return None
    return cleaned if isinstance(cleaned, str) else str(cleaned)


def _stated(value: Any) -> Optional[str]:
    """A real stated value, or None when the cell is a TBD-style placeholder."""
    text = _text(value)
    if text is None or text.lower() in _PLACEHOLDERS:
        return None
    return text


def _parse_workbook(blob: bytes) -> dict:
    """{'sheet', 'preamble', 'rows'} from the first worksheet.

    The header row is found by scanning for the row whose first cell reads
    'Tracking No.' (two banner rows precede it in the real file; the scan
    tolerates drift in that count). First cells of the rows above the header
    become 'preamble' strings; data rows become header-keyed dicts with
    dates as ISO strings. This is the exact shape the recorded JSON fixture
    stores, so the fixture slots into the normalize path where the xlsx
    parse leaves off.
    """
    workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        preamble: "list[str]" = []
        header: "list[str]" = []
        for _ in range(_HEADER_SCAN_ROWS):
            cells = next(rows_iter, None)
            if cells is None:
                break
            if _canon_header(cells[0] if cells else None) in (
                "tracking no.", "tracking no",
            ):
                header = [
                    str(cell) if cell is not None else "" for cell in cells
                ]
                break
            first = _text(cells[0] if cells else None)
            if first:
                preamble.append(first)
        if not header:
            raise ValueError(
                "unrecognized forecast workbook layout: no 'Tracking No.' "
                "header row in the first sheet"
            )
        rows: "list[dict]" = []
        for cells in rows_iter:
            if cells is None or all(cell is None for cell in cells):
                continue
            row: dict = {}
            for index, name in enumerate(header):
                value = cells[index] if index < len(cells) else None
                if isinstance(value, datetime):
                    value = value.date().isoformat()
                elif isinstance(value, date):
                    value = value.isoformat()
                row[name] = value
            rows.append(row)
        return {"sheet": str(sheet.title), "preamble": preamble, "rows": rows}
    finally:
        workbook.close()


def parse_meta(parsed: dict) -> dict:
    """{'as_of_date', 'fiscal_year', 'dept_funding_code'} from the preamble.

    The banner states the source's own snapshot date ('as of July 23, 2026');
    that is the data_as_of contract wants, never synthesized from now(). Any
    field the preamble does not state stays None.
    """
    joined = " ".join(parsed.get("preamble") or [])
    as_of_date: Optional[str] = None
    match = _AS_OF_RE.search(joined)
    if match:
        stamp = " ".join(match.group(1).replace(",", ", ").split())
        try:
            as_of_date = datetime.strptime(stamp, "%B %d, %Y").date().isoformat()
        except ValueError:
            as_of_date = None
    fiscal_year: Optional[str] = None
    fy = _FY_RE.search(joined)
    if fy:
        fiscal_year = "FY" + fy.group(1)
    elif re.match(r"^FY\d{4}$", str(parsed.get("sheet") or "")):
        fiscal_year = str(parsed["sheet"])
    code = _FUNDING_CODE_RE.search(joined)
    return {
        "as_of_date": as_of_date,
        "fiscal_year": fiscal_year,
        "dept_funding_code": code.group(1) if code else None,
    }


def _keyed(row: dict) -> dict:
    out: dict = {}
    for raw_name, value in row.items():
        canon = _canon_header(raw_name)
        key = _HEADER_KEYS.get(canon) or _fallback_key(canon)
        if key:
            out[key] = _clean_cell(value)
    return out


def _naics_codes(value: Any) -> "list[str]":
    """All 6-digit codes in the cell (the real file stores ints)."""
    stated = _stated(value)
    if not stated:
        return []
    return _NAICS_RE.findall(stated)


def _value_lower_bound(band: Any) -> Optional[float]:
    """Lower bound of the stated dollar band, in dollars.

    Only numbers explicitly present in the source string become pipeline
    dollars. '>= $1M and < $5M' -> 1_000_000.0; '>= $50M' -> 50_000_000.0.
    '< $15K' (16 rows in the real file) states no floor, so it parses to
    None rather than promoting the ceiling to a floor.
    """
    text = _stated(band)
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith(("below", "less than", "under", "<")):
        return None
    tokens = _MONEY_RE.findall(text)
    if not tokens:
        return None
    number, scale = tokens[0]
    if not scale:
        for _, later_scale in tokens[1:]:
            if later_scale:
                scale = later_scale
                break
    if not scale:
        return None
    return float(number.replace(",", "")) * _MULT[scale.upper()]


def _is_note_row(rec: dict) -> bool:
    """True for the file's trailing disclaimer/note rows.

    Those rows populate ONLY the first (Tracking No.) column with prose that
    is not a tracking number. They are file furniture, not lost records, so
    they never count as malformed drops.
    """
    populated = [key for key, value in rec.items() if _stated(value) is not None]
    if populated != ["tracking_no"]:
        return False
    text = _stated(rec.get("tracking_no")) or ""
    return not _TRACKING_RE.match(text)


def _hash_id(
    title: str, funding_office: Optional[str], quarter: Optional[str],
    ordinal: int,
) -> str:
    parts = [title.lower(), (funding_office or "").lower(), (quarter or "").lower()]
    if ordinal > 1:
        parts.append(str(ordinal))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return ID_PREFIX + digest


def _source_id(
    tracking: Optional[str],
    title: str,
    funding_office: Optional[str],
    quarter: Optional[str],
    ordinal: int,
) -> str:
    """'ed-fcst-fy26-ap-4186' from the file's own stable pre-award id.

    Tracking numbers are unique in the witnessed file (373 rows, zero
    repeats); a repeat in a future revision takes a file-order ordinal
    suffix instead of silently colliding in per-source dedupe. A row
    missing its tracking number falls back to a content hash.
    """
    if tracking and _TRACKING_RE.match(tracking):
        base = ID_PREFIX + tracking.lower()
        return base if ordinal <= 1 else base + "-" + str(ordinal)
    return _hash_id(title, funding_office, quarter, ordinal)


# --------------------------------------------------------------------------- #
# Query translation + normalize
# --------------------------------------------------------------------------- #
def _matches(rec: dict, codes: "list[str]", query: SourceQuery) -> bool:
    if query.naics_codes:
        wanted = [code.strip() for code in query.naics_codes if code and code.strip()]
        if wanted and not any(
            code.startswith(prefix) for code in codes for prefix in wanted
        ):
            return False
    if query.agencies:
        hay = " ".join(
            filter(
                None,
                [
                    AGENCY,
                    _text(rec.get("funding_office")),
                    _text(rec.get("contracting_office")),
                ],
            )
        ).lower()
        if not any(agency.lower() in hay for agency in query.agencies if agency):
            return False
    if query.set_asides:
        competition = (_text(rec.get("type_of_competition")) or "").lower()
        if not any(term.lower() in competition for term in query.set_asides if term):
            return False
    if query.keywords:
        hay = " ".join(
            filter(
                None,
                [
                    _text(rec.get("contract_name")),
                    _text(rec.get("naics_description")),
                    _text(rec.get("funding_office")),
                    _text(rec.get("contracting_office")),
                    _text(rec.get("requirement_type")),
                    _text(rec.get("incumbent_contractor_name")),
                ],
            )
        )
        if not matching_phrases(hay, query.keywords):
            return False
    return True


def _contacts(rec: dict) -> "list[OpportunityContact]":
    poc_name, poc_email = _stated(rec.get("poc_name")), _stated(rec.get("poc_email"))
    if poc_name or poc_email:
        return [
            OpportunityContact(name=poc_name, email=poc_email, contact_type="primary")
        ]
    return []


def _normalize(
    parsed: dict, workbook_url: str, query: SourceQuery
) -> "tuple[list[RawOpportunity], int, int]":
    """(matched opportunities in file order, malformed drops, note rows).

    Ids are assigned over every title-bearing row BEFORE filtering, so a
    narrower query never shifts which repeated line owns which ordinal.
    """
    meta = parse_meta(parsed)
    matched: "list[RawOpportunity]" = []
    dropped = 0
    notes = 0
    seen: "dict[str, int]" = {}
    for raw in parsed.get("rows") or []:
        rec = _keyed(raw)
        title = _stated(rec.get("contract_name"))
        if not title:
            if _is_note_row(rec):
                notes += 1
            else:
                dropped += 1
            continue
        tracking = _stated(rec.get("tracking_no"))
        funding_office = _stated(rec.get("funding_office"))
        quarter = _stated(rec.get("target_award_quarter"))
        if tracking and _TRACKING_RE.match(tracking):
            seen_key = tracking.lower()
        else:
            seen_key = "|".join(
                [title.lower(), (funding_office or "").lower(),
                 (quarter or "").lower()]
            )
        seen[seen_key] = seen.get(seen_key, 0) + 1
        codes = _naics_codes(rec.get("naics_raw"))
        if not _matches(rec, codes, query):
            continue
        payload = dict(rec)
        payload.update(
            {
                "naics_codes_all": codes,
                "fiscal_year": meta.get("fiscal_year"),
                "dept_funding_code": meta.get("dept_funding_code"),
                "source_url": workbook_url,
                "landing_url": LANDING_URL,
            }
        )
        matched.append(
            RawOpportunity(
                source=SOURCE_NAME,
                source_id=_source_id(
                    tracking, title, funding_office, quarter, seen[seen_key]
                ),
                title=title,
                agency=AGENCY,
                naics_code=codes[0] if codes else None,
                set_aside=_stated(rec.get("type_of_competition")),
                posted_date=None,
                response_deadline=None,
                estimated_value=_value_lower_bound(
                    rec.get("estimated_value_range")
                ),
                api_url=workbook_url,
                contacts=_contacts(rec),
                raw_payload=payload,
            )
        )
    return matched, dropped, notes


def _last_modified(headers: Optional[dict]) -> Optional[str]:
    """The workbook's Last-Modified date, the fallback when no banner date."""
    stamp = None
    for name, value in (headers or {}).items():
        if str(name).lower() == "last-modified":
            stamp = value
            break
    if not stamp:
        return None
    try:
        return parsedate_to_datetime(str(stamp)).date().isoformat()
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
class EdForecastSource(DataSource):
    name = SOURCE_NAME
    # Agency planning intent widens every client's forecast sweep, but it is
    # never a canonical live-opportunity discovery record.
    kind = SourceKind.ENRICHMENT
    enabled = True

    def __init__(
        self,
        fetch_text: Optional[Callable[..., str]] = None,
        fetch_bytes: Optional[Callable[..., "tuple[bytes, dict]"]] = None,
    ) -> None:
        from tools.toggles import is_enabled
        self.enabled = is_enabled(self.name, True)
        self.last_provenance: "Optional[dict]" = None
        # Injection seams for tests. Defaults resolve at call time through
        # the _http module attribute so the suite's live-HTTP receipt seam
        # observes real calls.
        self._fetch_text = fetch_text
        self._fetch_bytes = fetch_bytes

    def healthcheck(self) -> "tuple[bool, str]":
        fetch_text = self._fetch_text or _http.get_text
        try:
            html = fetch_text(LANDING_URL, timeout=8.0, retries=1)
        except Exception as exc:  # noqa: BLE001 - probe reports, never raises
            return False, f"ed.gov landing page unreachable: {exc}"
        workbook_url = discover_workbook_url(html)
        if not workbook_url:
            return False, (
                "landing page loads but no procurement-forecast .xlsx link "
                "found (layout drift or block page)"
            )
        return True, (
            "landing page live; current workbook: "
            + workbook_url.rsplit("/", 1)[-1]
        )

    def search(self, query: SourceQuery) -> "list[RawOpportunity]":
        fetch_text = self._fetch_text or _http.get_text
        fetch_bytes = self._fetch_bytes or _get_bytes
        try:
            html = fetch_text(LANDING_URL)
            workbook_url = discover_workbook_url(html)
            if not workbook_url:
                raise ValueError(
                    "landing page has no procurement-forecast .xlsx link"
                )
            blob, resp_headers = fetch_bytes(workbook_url)
            blob = bytes(blob or b"")
            if not blob.startswith(b"PK"):
                raise ValueError(
                    "workbook fetch returned non-xlsx bytes (block page?)"
                )
            parsed = _parse_workbook(blob)
        except Exception as exc:  # noqa: BLE001 - failure isolation contract
            LOG.warning("ed_forecast search failed: %s", exc)
            self.last_provenance = ProvenanceEnvelope(
                source=SOURCE_NAME,
                status="failed",
                mode="live_ed_forecast_xlsx",
                retrieval_mode="live",
                retrieved_at=datetime.now(timezone.utc),
                record_count=0,
                limitations=[f"forecast retrieval failed: {exc}"],
            ).model_dump(mode="json")
            return []

        matched, dropped, _notes = _normalize(parsed, workbook_url, query)
        limit = query.limit if query.limit and query.limit > 0 else len(matched)
        capped = cap_disclosed(matched, limit, key="items")
        kept: "list[RawOpportunity]" = capped["items"]

        limitations = [
            "Forecast lines are stated agency intent (P.L. 100-656 planning "
            "data), not live solicitations; ED's own disclaimer says every "
            "line is subject to revision or cancellation until the "
            "acquisition plan is completed",
        ]
        if dropped:
            limitations.append(
                f"dropped {dropped} rows with no contract name"
            )
        if capped.get("truncated"):
            limitations.append(str(capped.get("truncated_note")))
        envelope = ProvenanceEnvelope(
            source=SOURCE_NAME,
            status="partial" if dropped else "complete",
            mode="live_ed_forecast_xlsx",
            retrieval_mode="live",
            retrieved_at=datetime.now(timezone.utc),
            data_as_of=(
                parse_meta(parsed).get("as_of_date")
                or _last_modified(resp_headers)
            ),
            record_count=len(kept),
            limitations=limitations,
            public_detail=(
                "Live Department of Education procurement forecast workbook ("
                + workbook_url.rsplit("/", 1)[-1]
                + ")"
            ),
            retrieval_url=workbook_url,
            retrieval_query=query.model_dump(mode="json"),
            raw_content_sha256=hashlib.sha256(blob).hexdigest(),
            raw_content_kind="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            normalization_version="ed_forecast.v1.2026-08-08",
        )
        self.last_provenance = envelope.model_dump(mode="json")
        self.last_provenance.update({
            "source_url": workbook_url,
            "landing_url": LANDING_URL,
        })
        for opportunity in kept:
            opportunity.raw_payload["_provenance"] = self.last_provenance
        return kept

    def forecasts(self):
        from tools.api.forecasts.bridge import from_raw
        rows = self.search(SourceQuery(limit=5000))
        return [from_raw(row, self.last_provenance) for row in rows]


try:
    register_source(EdForecastSource)
except ValueError:
    # "ed_forecast" has no SourceSpec row in tools/api/source_catalog.py
    # yet. The integration lead adds the catalog row and the registrar import
    # together; guarding here keeps the module importable and testable now
    # without this lane editing shared files.
    pass
