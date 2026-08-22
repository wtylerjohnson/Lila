"""NIH RePORTER v2 adapter - the R&D contracts slice (no key).

WHY THIS SOURCE: NIH buys R&D through contracts, not only grants, and
RePORTER's contracts slice (activity codes N01/N02, plus the N43/N44 SBIR
contract phases) is the only pipeline source carrying PI names, project
titles with scientific context, and NIH Institute/Center (IC) attribution
for those awards. project_end_date is a recompete clock with scientific
context attached; task orders link to their IDIQ base through the
project_num stem. None of that exists in SAM.gov or USAspending payloads.

Endpoint: POST https://api.reporter.nih.gov/v2/projects/search
API home: https://api.reporter.nih.gov
Project pages: https://reporter.nih.gov/project-details/<appl_id>

Verifier caveats honored here (data/reference/source_registry.json,
"NIH RePORTER API v2 - R&D contracts slice"):
  - Soft rate limit ~1 request/second: every call is a single scoped POST
    (no fan-out, no pagination loop) with _http's bounded retry.
  - Max 500 records per request, max offset 14,999 per criteria set: this
    adapter never pages; it takes the first page in project_start_date
    descending order and discloses truncation against meta.total.
  - Join keys: 75N-series project_num stems are verbatim FPDS/USAspending
    PIIDs; legacy 26x-series stems need an HHSN prefix with the trailing
    letter preserved (261201500036I-0-... -> HHSN261201500036I). Sibling
    PIIDs differ only in that trailing letter, so the derived piid_hint
    always keeps the full stem including the letter. organization blocks
    carry primary_uei natively, so the UEI join is direct.
  - Newer OD-style project numbers (e.g. 92021A007...) match neither
    documented family; their piid_hint is None rather than a guess.

Scoping: criteria are always restricted to CONTRACT_ACTIVITY_CODES (the
funding-mechanism slice "R and D Contracts"); org names narrow via
RePORTER's contains-matching org_names criteria, keywords narrow via an
advanced text search over title/terms/abstract as OR'd quoted phrases,
and posted_from/posted_to narrow to federal fiscal years. This adapter
never bulk-mirrors the 54k-record contracts corpus.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api._http import post_json
from tools.api.provenance import ProvenanceEnvelope

logger = logging.getLogger(__name__)

SOURCE_NAME = "nih_reporter"
SEARCH_URL = "https://api.reporter.nih.gov/v2/projects/search"
PROJECT_URL_TMPL = "https://reporter.nih.gov/project-details/%s"
#: the CONTRACTS funding-mechanism slice. N01/N02 are R&D contracts; N43/N44
#: are SBIR contract phases. Registry-verified partition of RePORTER.
CONTRACT_ACTIVITY_CODES = ("N01", "N02", "N43", "N44")
#: slim server-side projection; every name verified live 2026-08-08
INCLUDE_FIELDS = (
    "ApplId", "ProjectNum", "Organization", "AwardAmount", "FiscalYear",
    "AgencyIcAdmin", "ProjectTitle", "ContactPiName", "ProjectStartDate",
    "ProjectEndDate", "ActivityCode", "FundingMechanism",
)
TEXT_SEARCH_FIELDS = "projecttitle,terms,abstracttext"
DEFAULT_CAP = 100
MAX_LIMIT = 500  # API hard cap per request
#: legacy RePORTER contract stem: 12 digits starting with 2, trailing letter
_LEGACY_STEM_RE = re.compile(r"^2\d{11}[A-Z]$")


def piid_hint(project_num: Any) -> Optional[str]:
    """RePORTER project number -> FPDS/USAspending PIID stem, or None.

    Only the two registry-verified families derive a hint:
      75N-series : the stem before the first '-' is a verbatim PIID
                   (75N91019D00024-0-... -> 75N91019D00024).
      legacy 26x : HHSN prefix, trailing letter preserved
                   (261201500036I-0-26100009-2 -> HHSN261201500036I).
    Anything else (e.g. 92021A007... OD-style numbers) returns None; a
    join hint is worth nothing unless it is exact.
    """
    stem = str(project_num or "").strip().upper().split("-")[0]
    if not stem:
        return None
    if stem.startswith("75N"):
        return stem
    if _LEGACY_STEM_RE.match(stem):
        return "HHSN" + stem
    return None


def _clean_text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _day(value: Any) -> Optional[str]:
    """'2025-09-30T00:00:00' -> '2025-09-30'; empty/None -> None."""
    text = _clean_text(value)
    return text[:10] if text else None


def _phrase_or(keywords: Optional[List[str]]) -> str:
    """Keyword lanes -> RePORTER 'advanced' operator text: OR'd quoted
    phrases, matching the house any-of keyword semantics."""
    phrases: List[str] = []
    for keyword in keywords or []:
        text = str(keyword or "").strip().strip('"').strip()
        if text:
            phrases.append('"%s"' % text)
    return " OR ".join(phrases)


def _federal_fy(day: date) -> int:
    """Federal fiscal year of a calendar date (FY starts October 1)."""
    return day.year + (1 if day.month >= 10 else 0)


def _fiscal_years(posted_from: Optional[date],
                  posted_to: Optional[date]) -> List[int]:
    """Posted-date window -> RePORTER fiscal_years criteria.

    RePORTER has no posted-date field; federal FY is its native clock. An
    open-ended from-window extends to the current FY; a to-only window
    keeps just that FY (an unbounded past is inexpressible here, per the
    base contract adapters ignore what they cannot express).
    """
    if posted_from is None and posted_to is None:
        return []
    if posted_from is None:
        start = end = _federal_fy(posted_to)  # type: ignore[arg-type]
    else:
        start = _federal_fy(posted_from)
        end = _federal_fy(posted_to) if posted_to else _federal_fy(
            datetime.now(timezone.utc).date())
    if start > end:
        start, end = end, start
    return list(range(start, end + 1))


def _build_body(query: SourceQuery, org_names: Optional[List[str]],
                cap: int) -> Dict[str, Any]:
    """SourceQuery (+ explicit org names) -> the exact POST body."""
    criteria: Dict[str, Any] = {
        "activity_codes": list(CONTRACT_ACTIVITY_CODES),
    }
    orgs = [str(org).strip() for org in (org_names or [])
            if str(org or "").strip()]
    if orgs:
        criteria["org_names"] = orgs  # RePORTER contains-matching
    text = _phrase_or(query.keywords)
    if text:
        criteria["advanced_text_search"] = {
            "operator": "advanced",
            "search_field": TEXT_SEARCH_FIELDS,
            "search_text": text,
        }
    years = _fiscal_years(query.posted_from, query.posted_to)
    if years:
        criteria["fiscal_years"] = years
    return {
        "criteria": criteria,
        "include_fields": list(INCLUDE_FIELDS),
        "offset": 0,
        "limit": cap,
        "sort_field": "project_start_date",
        "sort_order": "desc",
    }


def _slim(row: Any) -> Optional[Dict[str, Any]]:
    """One RePORTER result -> the project projection, or None if malformed.

    project_num is the identity key (and the PIID join basis), so a row
    without one is malformed and dropped. Damaged sub-blocks (organization,
    agency_ic_admin) degrade to None fields rather than dropping the row.
    """
    if not isinstance(row, Mapping):
        return None
    project_num = _clean_text(row.get("project_num"))
    if not project_num:
        return None
    org_block = row.get("organization")
    org_block = org_block if isinstance(org_block, Mapping) else {}
    ic_block = row.get("agency_ic_admin")
    ic_block = ic_block if isinstance(ic_block, Mapping) else {}
    amount = row.get("award_amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        amount = None
    fiscal_year = row.get("fiscal_year")
    if isinstance(fiscal_year, bool) or not isinstance(fiscal_year, int):
        fiscal_year = None
    appl_id = row.get("appl_id")
    url = PROJECT_URL_TMPL % appl_id if isinstance(appl_id, int) else None
    return {
        "project_num": project_num,
        "org": _clean_text(org_block.get("org_name")),
        "uei": _clean_text(org_block.get("primary_uei")),
        "amount": amount,
        "fy": fiscal_year,
        "ic": _clean_text(ic_block.get("abbreviation"))
        or _clean_text(ic_block.get("code")),
        "ic_name": _clean_text(ic_block.get("name")),
        "title": _clean_text(row.get("project_title")),
        "pi": _clean_text(row.get("contact_pi_name")),
        "activity_code": _clean_text(row.get("activity_code")),
        "funding_mechanism": _clean_text(row.get("funding_mechanism")),
        "start_date": _day(row.get("project_start_date")),
        "end_date": _day(row.get("project_end_date")),
        "piid_hint": piid_hint(project_num),
        "url": url,
    }


def _public_search_url(meta: Mapping[str, Any]) -> Optional[str]:
    """meta.properties.URL, with the API's own 'https:/' quirk repaired."""
    properties = meta.get("properties")
    if not isinstance(properties, Mapping):
        return None
    url = _clean_text(properties.get("URL"))
    if not url:
        return None
    if url.startswith("https:/") and not url.startswith("https://"):
        url = "https://" + url[len("https:/"):]
    return url


class NihReporterSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self,
                 poster: Optional[Callable[..., Any]] = None) -> None:
        self._post = poster or post_json

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use rd_contracts()")

    def healthcheck(self) -> Tuple[bool, str]:
        """One 1-record probe of the N01 slice; the light reflects reality."""
        try:
            data = self._post(SEARCH_URL, json={
                "criteria": {"activity_codes": ["N01"]},
                "include_fields": ["ApplId"],
                "offset": 0,
                "limit": 1,
            }, timeout=8.0, retries=1)
        except Exception as exc:  # noqa: BLE001 - probe reports, never raises
            return False, "endpoint unreachable: %s" % exc
        meta = data.get("meta") if isinstance(data, Mapping) else None
        total = meta.get("total") if isinstance(meta, Mapping) else None
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            return True, ("contracts slice reachable "
                          "(%d N01 projects)" % total)
        return False, "endpoint answered without a meta.total count"

    def enrich(self, query: SourceQuery) -> Dict[str, Any]:
        """Engine-facing alias: keyword/FY-scoped contracts slice. Engines
        that know the organization pass it to rd_contracts() directly."""
        return self.rd_contracts(query)

    def rd_contracts(self, query: SourceQuery,
                     org_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """NIH R&D contract projects matching the query, contracts-slice only.

        org_names narrow to organizations (contains match, e.g. 'LEIDOS'
        matches LEIDOS BIOMEDICAL RESEARCH, INC.); query.keywords narrow
        via title/terms/abstract text; posted_from/posted_to narrow to
        federal fiscal years. Returns {"projects": [...], "total_matched",
        "_provenance", ...}; each project carries project_num/org/amount/
        fy/ic/title plus the PI, the recompete-clock dates, the native
        UEI, its citation URL, and a derived piid_hint join key.
        """
        retrieved_at = datetime.now(timezone.utc)
        cap = max(1, min(int(query.limit or DEFAULT_CAP), MAX_LIMIT))
        body = _build_body(query, org_names, cap)
        try:
            data = self._post(SEARCH_URL, json=body, timeout=30.0, retries=3)
        except Exception as exc:  # noqa: BLE001 - failure isolation contract
            return self._failure(str(exc), retrieved_at)
        if not isinstance(data, Mapping):
            return self._failure(
                "non-JSON-object response from RePORTER", retrieved_at)

        meta = data.get("meta")
        meta = meta if isinstance(meta, Mapping) else {}
        results = data.get("results")
        results = results if isinstance(results, list) else []
        projects: List[Dict[str, Any]] = []
        dropped = 0
        for row in results:
            slim = _slim(row)
            if slim is None:
                dropped += 1
                continue
            projects.append(slim)

        total = meta.get("total")
        if isinstance(total, bool) or not isinstance(total, int):
            total = len(projects)
        total = max(total, len(projects))

        out: Dict[str, Any] = {
            "projects": projects,
            "total_matched": total,
            "api_url": SEARCH_URL,
            "search_url": _public_search_url(meta),
        }
        limitations = [
            "restricted to the R&D contracts slice (activity codes "
            "N01/N02/N43/N44); NIH grants are out of scope for this lane",
            "no per-response data-as-of stated by the API; NIH refreshes "
            "RePORTER on a weekly cycle",
        ]
        if total > len(projects):
            out["truncated"] = True
            note = ("kept first %d of %d matches in arrival order "
                    "(project_start_date descending)" % (len(projects), total))
            out["truncated_note"] = note
            limitations.append(note)
        if dropped:
            limitations.append(
                "%d malformed result rows dropped (no project_num)" % dropped)
        out["_provenance"] = ProvenanceEnvelope(
            source=self.name,
            status="complete",
            mode="live_nih_reporter_v2",
            retrieval_mode="live",
            retrieved_at=retrieved_at,
            data_as_of=None,
            record_count=len(projects),
            limitations=limitations,
            public_detail="NIH RePORTER v2 R&D contracts slice "
                          "(N01/N02/N43/N44 activity codes)",
        ).model_dump(mode="json")
        return out

    def _failure(self, message: str,
                 retrieved_at: datetime) -> Dict[str, Any]:
        return {
            "projects": [],
            "total_matched": 0,
            "error": message,
            "api_url": SEARCH_URL,
            "_provenance": ProvenanceEnvelope(
                source=self.name,
                status="failed",
                mode="live_nih_reporter_v2",
                retrieval_mode="live",
                retrieved_at=retrieved_at,
                record_count=0,
                limitations=[message],
            ).model_dump(mode="json"),
        }


def _register_when_cataloged() -> None:
    """Activate @register_source once the catalog knows this adapter.

    register_source refuses adapters without a SourceSpec row, and
    tools/api/source_catalog.py is a contract surface this connector lane
    does not edit. The lead lands the SourceSpec plus the registrar import;
    this guard makes that single catalog addition activate registration
    with no edit here, and keeps the module importable until then.
    """
    from tools.api.source_catalog import SOURCE_BY_ADAPTER
    if NihReporterSource.name in SOURCE_BY_ADAPTER:
        register_source(NihReporterSource)
    else:
        logger.info(
            "nih_reporter: registration deferred until a SourceSpec row "
            "exists in tools/api/source_catalog.py (lead wiring)")


_register_when_cataloged()
