"""OMB Public Apportionment System adapter: approved apportionments per TAFS.

Apportionment is the step where OMB legally releases budget authority to an
agency, account by account, BEFORE any solicitation or award exists. The
public files carry what no pipeline source has (USAspending has zero
apportionment data): ApprovedAmount by schedule line, Iteration cadence,
ApprovalTimestamp for each action, ApproverTitle, and the FundsProvidedBy
public-law citation. For the market map that turns into a leading demand
signal: money apportioned to an account is money the agency can now obligate.

Landing page (the only enumeration path; per-folder URLs 404):
    https://apportionment-public.max.gov/
Per-TAFS JSON files hang off it as
    /Fiscal Year {YYYY}/{Agency}/JSON/
        FY{YYYY}_Agency={A}_Bureau={B}_TAFS={T}_Iteration={N}_{Y-M-D-H.M}.json

Join keys downstream: ScheduleData CgacAgency + CgacAcct compose the
federal-account key USAspending exposes as agency_identifier +
main_account_code (credit financing accounts join only at agency level);
FundsProvidedBy citations join congress.gov.

Reality notes measured 2026-08-08 against the live index (30,553 JSON
files): about 5 percent of filenames deviate from the canonical stem
(Account= instead of TAFS=, missing Iteration, account token run together
with the date), so stem parsing is a tolerant ladder, never one regex. The
site was removed 2025-03-24 and restored 2025-08-15 under court order, so
takedown risk is real and rides every payload's limitations.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from tools.api._http import get_json, get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.api.provenance import ProvenanceEnvelope

INDEX_URL = "https://apportionment-public.max.gov/"
#: root-relative hrefs on the landing page; only the JSON tree is ingested
FILE_HREF_RE = re.compile(
    r'href="(/Fiscal%20Year%20\d{4}/[^"]+?/JSON/[^"]+?\.json)"')
_FY_RE = re.compile(r"^FY(\d{4})_")
_AGENCY_RE = re.compile(r"Agency=([^_]+)")
_BUREAU_RE = re.compile(r"Bureau=(.+?)(?:_TAFS=|_Account=|_Iteration=|$)")
_TAFS_RE = re.compile(r"(?:TAFS|Account)=(.+?)(?:_Iteration=|$)")
_ITER_RE = re.compile(r"Iteration=(\d+)")
#: trailing approval token, e.g. 2025-07-24-17.00; anchored at the end so the
#: run-together "Account=012-06002025-09-07-12.34" variant still splits clean
_DATE_TOKEN_RE = re.compile(r"(20\d{2}-\d{2}-\d{2}-\d{2}\.\d{2})$")

#: total budgetary resources available: the apportioned total for the file.
#: 6190 is the application-side total; 1920 is the resources-side fallback.
TOTAL_LINES = ("6190", "1920")

#: per-call budget: one index read plus at most this many per-TAFS file GETs
MAX_ROWS = 8
INDEX_TIMEOUT = 120.0
FILE_TIMEOUT = 20.0

ENUMERATION_LIMIT = (
    "per-folder URLs 404; enumeration rides the ~20MB landing-page index, "
    "so every call re-reads the full file tree"
)
TAKEDOWN_LIMIT = (
    "OMB removed this site 2025-03-24 and restored it 2025-08-15 under "
    "court order; footnotes are sometimes withheld as predecisional, so "
    "footnote coverage is patchy and takedown risk recurs"
)


def _parse_stem(stem: str) -> Optional[dict]:
    """Tolerant ladder over one filename stem (no .json suffix).

    Canonical: FY2025_Agency=SBA_Bureau=SBA_TAFS=073-X-0100_Iteration=4_
    2025-07-24-17.00. Real deviations kept: Account= for TAFS=, missing
    Iteration, bureau-level files with no account token, and the account
    token run together with the date. Returns None only when the stem
    carries neither a fiscal year nor an agency token.
    """
    fy_m = _FY_RE.match(stem)
    agency_m = _AGENCY_RE.search(stem)
    if not fy_m or not agency_m:
        return None
    date_m = _DATE_TOKEN_RE.search(stem)
    token = date_m.group(1) if date_m else None
    body = stem[: date_m.start()] if date_m else stem
    body = body.rstrip("_-")
    bureau_m = _BUREAU_RE.search(body)
    tafs_m = _TAFS_RE.search(body)
    iter_m = _ITER_RE.search(body)
    tafs = tafs_m.group(1).rstrip("_-") if tafs_m else None
    return {
        "fy": int(fy_m.group(1)),
        "agency_abbr": agency_m.group(1),
        "bureau_abbr": bureau_m.group(1) if bureau_m else None,
        "tafs": tafs or None,
        "iteration": int(iter_m.group(1)) if iter_m else None,
        "date_token": token,
    }


def _token_date(token: Optional[str]) -> Optional[str]:
    """ISO date from a filename approval token; None on garbage months."""
    if not token:
        return None
    try:
        return datetime.strptime(token, "%Y-%m-%d-%H.%M").date().isoformat()
    except ValueError:
        return None


def _parse_index(html: str) -> tuple[list[dict], int]:
    """Every JSON file entry in the landing-page tree, plus a dropped count.

    A stem the ladder cannot identify is dropped and counted, never raised.
    """
    from urllib.parse import unquote

    entries: list[dict] = []
    dropped = 0
    for href in FILE_HREF_RE.findall(html or ""):
        decoded = unquote(href)
        parts = decoded.strip("/").split("/")
        if len(parts) < 4:
            dropped += 1
            continue
        meta = _parse_stem(parts[-1][: -len(".json")])
        if meta is None:
            dropped += 1
            continue
        meta["agency"] = parts[1]
        meta["file_url"] = INDEX_URL.rstrip("/") + href
        meta["file_name"] = parts[-1]
        entries.append(meta)
    return entries, dropped


def _matches(entry: dict, terms: list[str]) -> bool:
    """Agency-term match: folder-name substring, or exact abbreviation or
    CGAC agency code, so "small business", "SBA", and "073" all land."""
    folder = str(entry.get("agency") or "").lower()
    abbr = str(entry.get("agency_abbr") or "").lower()
    bureau = str(entry.get("bureau_abbr") or "").lower()
    tafs = str(entry.get("tafs") or "")
    cgac = tafs.split("-")[0].lower() if tafs else ""
    for term in terms:
        if term in folder or term == abbr or term == bureau or term == cgac:
            return True
    return False


def _total_amount(schedule: Any) -> Optional[float]:
    """The file's apportioned total from line 6190, falling back to 1920."""
    if not isinstance(schedule, list):
        return None
    for line_no in TOTAL_LINES:
        found = []
        for row in schedule:
            if not isinstance(row, dict):
                continue
            if str(row.get("LineNumber") or "").strip() != line_no:
                continue
            amount = row.get("ApprovedAmount")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                continue
            found.append(amount)
        if found:
            return sum(found)
    return None


def _first_value(schedule: Any, key: str) -> Optional[str]:
    if not isinstance(schedule, list):
        return None
    for row in schedule:
        if isinstance(row, dict) and str(row.get(key) or "").strip():
            return str(row[key]).strip()
    return None


def _file_approved_date(doc: dict) -> Optional[str]:
    """ISO date from ApprovalTimestamp (DB2 style 2025-07-24-17.00.32...)."""
    stamp = str(doc.get("ApprovalTimestamp") or "")[:10]
    try:
        return datetime.strptime(stamp, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


class OmbApportionmentSource(DataSource):
    name = "omb_apportionment"
    kind = SourceKind.ENRICHMENT

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError(
            "enrichment-only source; use apportionments()")

    def healthcheck(self) -> tuple[bool, str]:
        """Ranged GET of the index head: reachability without the 20MB body
        (Accept-Ranges verified live 2026-08-08, HTTP 206)."""
        try:
            head = get_text(INDEX_URL, headers={"Range": "bytes=0-2047"},
                            timeout=8.0, retries=1)
        except Exception as exc:  # noqa: BLE001
            return False, f"index page unreachable: {exc}"
        if "pportionment" in head:
            return True, "index page reachable (ranged probe)"
        return False, f"unexpected index head: {head[:80]!r}"

    def apportionments(self, query: SourceQuery) -> dict[str, Any]:
        """Approved apportionment actions for the query's agency terms.

        Returns {'rows': [...], 'total_matched': N, 'index_url': ...,
        '_provenance': ...}. Each row: agency, bureau, account, tafs, fy,
        iteration, amount (line 6190 total, 1920 fallback), approved_date,
        funds_provided_by, approver_title, footnote_count, file_url (the
        citation). Terms come from query.agencies (query.keywords as the
        fallback lane); posted_from/posted_to bound the APPROVAL date.
        Matches sort newest approval first (fiscal year, then timestamp)
        and the kept slice is capped at min(query.limit, MAX_ROWS) with the
        cut disclosed, because each kept row costs one bounded file GET.
        """
        terms = [t.strip().lower() for t in
                 (query.agencies or query.keywords or []) if t.strip()]
        if not terms:
            return self._failed_payload(
                "unscoped query refused: the index spans about 30,000 "
                "apportionment files across every agency; pass agencies "
                "or keywords"
            )
        try:
            html = get_text(INDEX_URL, timeout=INDEX_TIMEOUT, retries=2)
        except Exception as exc:  # noqa: BLE001
            return self._failed_payload(f"index fetch failed: {exc}")

        entries, dropped = _parse_index(html)
        matched = [e for e in entries if _matches(e, terms)]

        undated_excluded = 0
        if query.posted_from or query.posted_to:
            windowed = []
            for entry in matched:
                day = _token_date(entry.get("date_token"))
                if day is None:
                    undated_excluded += 1
                    continue
                if query.posted_from and day < query.posted_from.isoformat():
                    continue
                if query.posted_to and day > query.posted_to.isoformat():
                    continue
                windowed.append(entry)
            matched = windowed

        # newest approval first; both passes stable so the order is total
        matched.sort(key=lambda e: (e.get("date_token") or "",
                                    e.get("file_name") or ""), reverse=True)
        matched.sort(key=lambda e: e.get("fy") or 0, reverse=True)

        cap = max(1, min(query.limit or MAX_ROWS, MAX_ROWS))
        payload = cap_disclosed(matched, cap, key="rows")

        rows: list[dict] = []
        fetch_failures = 0
        for entry in payload["rows"]:
            row = {
                "agency": entry.get("agency"),
                "agency_abbr": entry.get("agency_abbr"),
                "bureau": entry.get("bureau_abbr"),
                "account": None,
                "tafs": entry.get("tafs"),
                "fy": entry.get("fy"),
                "iteration": entry.get("iteration"),
                "amount": None,
                "approved_date": _token_date(entry.get("date_token")),
                "file_url": entry.get("file_url"),
            }
            try:
                doc = get_json(row["file_url"], timeout=FILE_TIMEOUT,
                               retries=2)
                if not isinstance(doc, dict):
                    raise ValueError("file payload is not a JSON object")
                schedule = doc.get("ScheduleData")
                row["amount"] = _total_amount(schedule)
                row["account"] = _first_value(schedule, "AccountTitle")
                row["bureau"] = (_first_value(schedule, "BudgetBureauTitle")
                                 or row["bureau"])
                row["approved_date"] = (_file_approved_date(doc)
                                        or row["approved_date"])
                row["funds_provided_by"] = doc.get("FundsProvidedBy") or None
                row["approver_title"] = doc.get("ApproverTitle") or None
                footnotes = doc.get("FootnoteData")
                row["footnote_count"] = (len(footnotes)
                                         if isinstance(footnotes, list)
                                         else 0)
            except Exception as exc:  # noqa: BLE001
                fetch_failures += 1
                row["fetch_error"] = str(exc)
            rows.append(row)
        payload["rows"] = rows

        limitations = [ENUMERATION_LIMIT, TAKEDOWN_LIMIT]
        if dropped:
            limitations.append(
                f"{dropped} index filenames unparseable and dropped")
        if undated_excluded:
            limitations.append(
                f"{undated_excluded} undated files excluded by the "
                "approval-date window")
        if fetch_failures:
            limitations.append(
                f"{fetch_failures} of {len(rows)} kept rows failed the "
                "per-file fetch; filename fields retained")

        approved_days = [r["approved_date"] for r in rows
                         if r.get("approved_date")]
        payload["index_url"] = INDEX_URL
        payload["_provenance"] = ProvenanceEnvelope(
            source=self.name,
            status=("partial" if (fetch_failures or dropped
                                  or undated_excluded) else "complete"),
            mode="live_index_scan",
            retrieval_mode="live",
            retrieved_at=datetime.now(timezone.utc),
            data_as_of=max(approved_days) if approved_days else None,
            record_count=len(rows),
            limitations=limitations,
        ).model_dump(mode="json")
        return payload

    def _failed_payload(self, message: str) -> dict[str, Any]:
        """Honest failure shape: the error rides the payload, never raises."""
        return {
            "rows": [],
            "total_matched": 0,
            "error": message,
            "index_url": INDEX_URL,
            "_provenance": ProvenanceEnvelope(
                source=self.name,
                status="failed",
                mode="live_index_scan",
                retrieval_mode="live",
                retrieved_at=datetime.now(timezone.utc),
                record_count=0,
                limitations=[message],
            ).model_dump(mode="json"),
        }


try:
    register_source(OmbApportionmentSource)
except ValueError:
    # omb_apportionment has no SourceSpec row in tools/api/source_catalog.py
    # yet; the integration lead wires the catalog row and the registrar
    # import together. Until then the adapter stays importable but
    # unregistered, so tests and engines constructing it directly work.
    pass
