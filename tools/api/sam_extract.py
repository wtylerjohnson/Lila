"""SAM.gov daily extract adapter — the QUOTA-FREE discovery path.

GSA publishes the entire active contract-opportunity universe as a daily CSV
(Data Services → Contract Opportunities → datagov). Downloading it costs zero
API quota: no key, no rate limit, no 10/day ceiling. One download per day,
then UNLIMITED local searches — every NAICS lane and every keyword screened
against every active notice, in one pass, for free.

Dataset: https://catalog.data.gov/dataset/contract-opportunities-from-sam-gov
File:    https://falextracts.s3.amazonaws.com/Contract Opportunities/datagov/
         ContractOpportunitiesFullCSV.csv  (refreshed nightly by GSA)

Trade-off vs the live API: up to ~24h staleness. For capture work that's
nothing — notices live for weeks. The live API remains the freshness verifier;
this is the workhorse.

OUTAGE DOCTRINE (2026-08-04). From 2026-07-29 the nightly export published a
header-only 685-byte object at this same key for a week. Diagnosis confirmed
the URL/naming/location did NOT change: the Data Services tree, FSD KB
article, data.gov, and sam.gov's mediated download endpoint all still name
this exact key; no date-stamped daily snapshot exists anywhere on
falextracts; prior S3 versions are not anonymously readable. So when the
floor refuses a day, do not go hunting for a moved file: the file is empty at
the source, the LaunchAgent retries every morning, and departures that the
missed days would have shown are recoverable from the weekly FY archive via
tools/sam_archive_backfill.py (run it after the Sunday build; the daily heals
forward on its own once GSA fixes the export).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import httpx

from agents.schemas import OpportunityContact, RawOpportunity
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.text_match import phrase_pattern as _keyword_pattern

EXTRACT_URL = os.environ.get(
    "LILA_SAM_EXTRACT_URL",
    "https://falextracts.s3.amazonaws.com/Contract%20Opportunities/datagov/"
    "ContractOpportunitiesFullCSV.csv",
)
_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "sam_extract"
# ARCHIVE, not cache: a rotated extract is the only copy that will ever
# exist of that day, so it lives outside the disposable directory.
_DEFAULT_ARCHIVE = Path(__file__).resolve().parents[2] / "data" / "archive" / "sam_extract"
# SNIPPET cap only (2026-07-30): the keyword SCREEN and the attachment
# anchors now read the complete description; this constant bounds only the
# description_snippet carried in downstream payloads, where a 32K statement
# of work would balloon triage prompts without adding screen recall.
SCREEN_DESCRIPTION_CHARS = 2000
SCREEN_EVIDENCE_CONTEXT_CHARS = 180
MAX_SCREEN_EVIDENCE_MATCHES = 12
_ATTACHMENT_ANCHOR_STOP = frozenset({
    "aided", "application", "applications", "case", "computer", "end",
    "enterprise", "federal", "first", "full", "management", "operations",
    "platform", "public", "reporting", "safety", "service", "services",
    "software", "solution", "solutions", "support", "system", "systems",
    "technology", "technologies", "user", "world",
})
# L17: the old MAX_RESULTS = 300 truncated matches mid-file in CSV order —
# every broad sweep reported exactly 300, a ceiling reading as a census.
# The screen now returns EVERY match and reports the census (last_census);
# any downstream cut must reconcile as "screened X of Y", never silently.


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_SAM_EXTRACT_DIR", str(_DEFAULT_DIR)))


def _today_path() -> Path:
    return _cache_dir() / f"opportunities_{date.today().isoformat()}.csv"


def _latest_path() -> Optional[Path]:
    """The newest CREDIBLE extract on disk, or None.

    Below-floor files are skipped, not served: a header-only extract read by
    the events lane produced "0 event-language notices screened", which
    reads as a quiet market rather than a broken input. No extract at all is
    the honest answer, and the lane already discloses that state.
    """
    from tools.notice_store import min_credible_extract_bytes

    d = _cache_dir()
    if not d.exists():
        return None
    floor = min_credible_extract_bytes()
    for path in sorted(d.glob("opportunities_*.csv"), reverse=True):
        if path.stat().st_size >= floor:
            return path
    return None


class ExtractRefused(RuntimeError):
    """The upstream object is not a credible daily extract. Raised INSTEAD of
    promoting the payload, so a broken upstream day leaves no file behind
    for the idempotent skip to trust or for _latest_path to serve."""


class ExtractReadFailed(OSError):
    """A consumed extract changed or was not read completely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _select_extract(census: dict) -> Path:
    """Observe the native daily selection without overriding its clock/path."""
    selection = {
        "selection_started_at_utc": _utc_now(),
        "selection_started_local_date": date.today().isoformat(),
        "status": "selecting",
    }
    census["extract_selection"] = selection
    try:
        path = download_extract()
        selection["status"] = "selected"
        return path
    except Exception as exc:
        selection.update(status="failed", error_type=type(exc).__name__,
                         error=str(exc)[:240])
        raise
    finally:
        selection["selection_finished_local_date"] = date.today().isoformat()
        selection["selection_finished_at_utc"] = _utc_now()


def _file_stat(stat) -> dict:
    return {
        "device": stat.st_dev, "inode": stat.st_ino,
        "size_bytes": stat.st_size, "modified_at_ns": stat.st_mtime_ns,
        "changed_at_ns": stat.st_ctime_ns,
    }


class _HashingReader(io.RawIOBase):
    """Hash the bytes delivered to the CSV decoder, not a later path reopen."""

    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
        self.bytes_read = 0
        self.eof = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        count = self.source.readinto(buffer)
        if count:
            self.digest.update(memoryview(buffer)[:count])
            self.bytes_read += count
        elif count == 0:
            self.eof = True
        return count


@contextmanager
def _read_extract(path: Path, census: dict, scan: str, *, previous=None):
    """Receipt each full scan; two-pass callers may bind to their first read."""
    path = Path(path).absolute()
    match = re.fullmatch(r"opportunities_(\d{4}-\d{2}-\d{2})\.csv", path.name)
    receipt = {
        "schema_version": 1, "scan": scan, "path": str(path),
        "filename": path.name, "cache_date": match.group(1) if match else None,
        "cache_date_basis": "local_cache_filename_not_upstream_publication",
        **{key: value for key, value in census["extract_selection"].items()
           if key.startswith("selection_")},
        "read_started_at_utc": _utc_now(), "read_finished_at_utc": None,
        "status": "reading", "complete": False, "integrity": "not_checked",
        "sha256": None, "bytes_read": 0,
    }
    census["extract_receipts"].append(receipt)
    try:
        with open(path, "rb", buffering=0) as source:
            before = _file_stat(os.fstat(source.fileno()))
            receipt["file_stat_before"] = before
            receipt["path_stat_before"] = _file_stat(path.stat())
            if receipt["path_stat_before"] != before or (
                    previous is not None
                    and previous["file_stat_after"] != before):
                receipt["integrity"] = "changed"
                raise ExtractReadFailed("extract changed before scan")
            reader = _HashingReader(source)
            with io.TextIOWrapper(io.BufferedReader(reader), encoding="utf-8",
                                  errors="replace", newline="") as text:
                try:
                    yield text
                finally:
                    receipt["sha256"] = reader.digest.hexdigest()
                    receipt["bytes_read"] = reader.bytes_read
                    receipt["file_stat_after"] = _file_stat(
                        os.fstat(source.fileno()))
                    try:
                        receipt["path_stat_after"] = _file_stat(path.stat())
                    except OSError:
                        receipt["path_stat_after"] = None
                    if (receipt["file_stat_after"] != before
                            or receipt["path_stat_after"] != before):
                        receipt["integrity"] = "changed"
                if receipt["integrity"] == "changed":
                    raise ExtractReadFailed("extract changed during scan")
                if not reader.eof or reader.bytes_read != before["size_bytes"]:
                    raise ExtractReadFailed("extract scan did not reach full EOF")
                if previous is not None and (
                        receipt["sha256"] != previous["sha256"]):
                    receipt["integrity"] = "changed"
                    raise ExtractReadFailed("extract bytes changed between scans")
        receipt.update(status="verified", complete=True, integrity="stable")
    except Exception as exc:
        receipt.update(status="failed", error_type=type(exc).__name__,
                       error=str(exc)[:240])
        if receipt["integrity"] != "changed":
            receipt["integrity"] = "read_failed"
        raise
    finally:
        receipt["read_finished_at_utc"] = _utc_now()


def _refusal_evidence() -> str:
    """What the upstream is actually serving, for the failure message.

    Measured 2026-07-30: GSA's nightly export published a 685-byte
    header-only object two nights running (Content-Length: 685). The log
    line must let an operator distinguish "our download broke" from "their
    export broke" without re-deriving it.
    """
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True,
                          transport=httpx.HTTPTransport(
                              local_address="0.0.0.0")) as c:
            head = c.head(EXTRACT_URL)
        return (f"upstream Content-Length="
                f"{head.headers.get('content-length', '?')}, "
                f"Last-Modified={head.headers.get('last-modified', '?')}")
    except Exception as exc:  # noqa: BLE001 - evidence is best-effort
        return f"upstream HEAD failed ({type(exc).__name__})"


def download_extract(force: bool = False, *, attempts: int = 3,
                     backoff_s: float = 60.0) -> Path:
    """Fetch today's extract once; reuse for the rest of the day.

    VALIDATE BEFORE PROMOTE (2026-07-30). The GSA nightly export published a
    header-only 685-byte object two nights running; this function streamed
    it, renamed it into place, and reported success ("0 MB in 1s"), and the
    idempotent skip then served the poison file for the whole day, so even a
    later good upstream rewrite was never fetched. Now a payload below the
    store's credibility floor is never promoted: the attempt retries with
    backoff, a final failure raises ExtractRefused with the upstream's own
    headers as evidence, and an ALREADY-PRESENT below-floor file is treated
    as absent and removed, so the first caller after upstream recovers heals
    the day instead of trusting yesterday's poison. The floor is the notice
    store's own (min_credible_extract_bytes): one owner, both gates.
    """
    from tools.notice_store import min_credible_extract_bytes

    floor = min_credible_extract_bytes()
    target = _today_path()
    if target.exists() and not force:
        if target.stat().st_size >= floor:
            return target
        print(f"[sam-extract] {target.name} exists at "
              f"{target.stat().st_size:,} bytes, below the {floor:,}-byte "
              f"floor; discarding it and re-downloading", file=sys.stderr)
        target.unlink()
    _cache_dir().mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    print("[sam-extract] downloading today's full extract (large file, "
          "one-time per day) ...", file=sys.stderr)
    last_size = 0
    for attempt in range(1, attempts + 1):
        t0 = time.monotonic()
        with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0),
                          transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                          follow_redirects=True) as c:
            with c.stream("GET", EXTRACT_URL) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_bytes(1 << 20):
                        f.write(chunk)
        last_size = tmp.stat().st_size
        if last_size >= floor:
            break
        tmp.unlink(missing_ok=True)
        print(f"[sam-extract] attempt {attempt}/{attempts}: payload is "
              f"{last_size:,} bytes, below the {floor:,}-byte floor; "
              f"refusing to promote it", file=sys.stderr)
        if attempt < attempts:
            time.sleep(backoff_s)
    else:
        raise ExtractRefused(
            f"daily extract refused after {attempts} attempts: payload "
            f"{last_size:,} bytes is below the {floor:,}-byte floor "
            f"({_refusal_evidence()}); no file promoted, next invocation "
            f"will retry")
    tmp.rename(target)
    mb = target.stat().st_size / 1e6
    print(f"[sam-extract] {mb:.0f} MB in {time.monotonic()-t0:.0f}s -> {target.name}",
          file=sys.stderr)
    # keep only the newest two days of extracts UNCOMPRESSED, and archive the
    # rest rather than deleting them.
    #
    # Every rotated file used to be gone permanently. There is no way to ask
    # sam.gov for last Tuesday's extract: the S3 object is overwritten
    # nightly, so a day we dropped is a day nobody can reconstruct. That is
    # the same loss the notice store exists to prevent, one level up. The
    # 07-24 file rotated out before the store existed and its ids can never
    # enter it.
    #
    # The two-day working set is UNCHANGED. _latest_path and everything that
    # reads the extract still see exactly what they saw before; this only
    # decides what happens to the third-oldest file.
    for old in sorted(_cache_dir().glob("opportunities_*.csv"))[:-2]:
        try:
            archive_extract(old)
        except Exception as exc:  # noqa: BLE001 - archiving never fails a pull
            print(f"[sam-extract] archive failed for {old.name} "
                  f"({type(exc).__name__}); leaving the file in place rather "
                  f"than deleting it", file=sys.stderr)
    return target


def _archive_dir() -> Path:
    return Path(os.environ.get("LILA_SAM_EXTRACT_ARCHIVE_DIR",
                               str(_DEFAULT_ARCHIVE)))


def archive_extract(path: Path) -> Optional[Path]:
    """Compress one rotated extract into the archive, then drop the CSV.

    Returns the archive path, or None when the file was already archived or
    was below-floor poison. The source CSV is removed ONLY after the
    compressed copy is closed and non-empty, so an interrupted archive costs
    disk, never the day.

    THE ARCHIVE ADMITS NO POISON (2026-08-04). Twice during the July-August
    outage a 685-byte header-only file was promoted by a process running with
    a lowered floor, and rotation then gzipped it into the PERMANENT archive,
    where it claimed to be the only copy of a day that was never captured.
    The archive is the store's upstream of last resort; a below-floor member
    corrupts backfills silently. Poison is discarded here, at the one owner
    of admission, mirroring download_extract's discard-below-floor rule.
    """
    from tools.notice_store import min_credible_extract_bytes

    path = Path(path)
    size = path.stat().st_size
    floor = min_credible_extract_bytes()
    if size < floor:
        path.unlink(missing_ok=True)
        print(f"[sam-extract] {path.name} is {size:,} bytes, below the "
              f"{floor:,}-byte floor; discarded rather than archived",
              file=sys.stderr)
        return None
    target = _archive_dir() / (path.name + ".gz")
    if target.exists() and target.stat().st_size > 0:
        path.unlink(missing_ok=True)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".gz.part")
    t0 = time.monotonic()
    expected = path.stat().st_size
    copied = 0
    try:
        with open(path, "rb") as src, gzip.open(part, "wb", compresslevel=6) as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
        # COUNT THE BYTES, do not infer from the output size. gzip writes a
        # ~20-byte header even for empty input, so "the archive is non-empty"
        # is not evidence that anything was copied. The source is deleted
        # immediately after this check, so the check has to be the real one.
        if copied != expected:
            raise OSError(f"archive of {path.name} copied {copied} of "
                          f"{expected} bytes")
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.rename(target)
    raw, gz = path.stat().st_size, target.stat().st_size
    path.unlink(missing_ok=True)
    print(f"[sam-extract] archived {path.name}: {raw/1e6:.0f} MB -> "
          f"{gz/1e6:.1f} MB ({gz/raw*100:.1f}%) in {time.monotonic()-t0:.0f}s",
          file=sys.stderr)
    return target


def _norm_header(h: str) -> str:
    return "".join(ch for ch in (h or "").lower() if ch.isalnum())


# normalized-header -> canonical field
_COLS = {
    "noticeid": "notice_id",
    "title": "title",
    "sol": "solicitation",
    "solnumber": "solicitation",
    "departmentindagency": "agency",
    "subtier": "subtier",
    "office": "office",
    "posteddate": "posted",
    "type": "type",
    "setaside": "set_aside",
    "setasidecode": "set_aside_code",
    "responsedeadline": "deadline",
    "naicscode": "naics",
    "classificationcode": "psc",
    "popstreetaddress": "pop_street",
    "popcity": "pop_city",
    "popstate": "pop_state",
    "popzip": "pop_zip",
    "popcountry": "pop_country",
    "active": "active",
    "link": "url",
    "description": "description",
    # Columns the extract has always carried and we discarded. Added
    # 2026-07-28, mapping only: iter_rows yields them, every existing consumer
    # uses .get() and is unaffected.
    "awardee": "awardee",
    "awardnumber": "award_number",
    "awarddate": "award_date",
    "award": "award_dollars",          # header is "Award$"; $ normalizes away
    "archivetype": "archive_type",
    "archivedate": "archive_date",
    "basetype": "base_type",
    "secondarycontactemail": "poc_secondary_email",
    "secondarycontactfullname": "poc_secondary_name",
    "primarycontactfullname": "poc_name",
    "primarycontactemail": "poc_email",
    "primarycontactphone": "poc_phone",
    "primarycontacttitle": "poc_title",
    # The last 13 unmapped columns (audit 2026-07-30): every header the
    # extract carries now has a canonical name. WE PRESERVE DATA DETAIL:
    # parse-time drops are irreversible once the extract rotates, and a
    # column nobody needs today (a fax number) costs bytes, while a column
    # somebody needs next quarter (office state, organization type) costs a
    # backfill that may no longer be possible.
    "cgac": "cgac",
    "fpdscode": "fpds_code",
    "aaccode": "aac_code",
    "primarycontactfax": "poc_fax",
    "secondarycontacttitle": "poc_secondary_title",
    "secondarycontactphone": "poc_secondary_phone",
    "secondarycontactfax": "poc_secondary_fax",
    "organizationtype": "organization_type",
    "state": "office_state",
    "city": "office_city",
    "zipcode": "office_zip",
    "countrycode": "office_country",
    "additionalinfolink": "info_link",
}


def _parse_dt(value: str) -> Optional[date]:
    v = (value or "").strip()
    if len(v) >= 10:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _posted_order(value: str) -> float:
    """Chronological posting order, retaining same-day amendment times."""
    text = (value or "").strip()
    if not text:
        return 0.0
    if re.search(r"[+-]\d{2}$", text):
        text += ":00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def iter_rows(fh: Iterable[str]) -> Iterable[dict]:
    """Yield canonical dicts from the extract, tolerant of column drift."""
    reader = csv.reader(fh)
    header = next(reader, None)
    if not header:
        return
    idx = {}
    for i, h in enumerate(header):
        canon = _COLS.get(_norm_header(h))
        if canon and canon not in idx:
            idx[canon] = i
    for row in reader:
        if not row:
            continue
        yield {k: (row[i].strip() if i < len(row) else "") for k, i in idx.items()}


def _to_opportunity(r: dict) -> RawOpportunity:
    contacts = []
    if r.get("poc_name") or r.get("poc_email"):
        contacts.append(OpportunityContact(
            name=r.get("poc_name") or None, title=r.get("poc_title") or None,
            email=r.get("poc_email") or None, phone=r.get("poc_phone") or None,
            contact_type="primary",
        ))
    agency = " / ".join(x for x in [r.get("agency"), r.get("subtier")] if x) or None
    return RawOpportunity(
        source="sam.gov",  # same downstream lane as the live API — same notices
        source_id=r.get("notice_id") or r.get("solicitation") or "",
        title=r.get("title") or "",
        agency=agency,
        naics_code=r.get("naics") or None,
        psc_code=r.get("psc") or None,
        set_aside=r.get("set_aside") or None,
        posted_date=_parse_dt(r.get("posted", "")),
        response_deadline=_parse_dt(r.get("deadline", "")),
        estimated_value=None,
        api_url=r.get("url") or None,
        contacts=contacts,
        raw_payload={k: v for k, v in r.items() if k != "description"}
        | {"description_snippet":
           (r.get("description") or "")[:SCREEN_DESCRIPTION_CHARS],
           "via": "daily-extract"},
    )


def _screen_evidence(title: str, description: str,
                     keywords: list[str]) -> list[dict]:
    """Preserve bounded verbatim context for full-description term hits.

    The daily extract screen reads the complete description while the
    downstream payload deliberately keeps only a short leading snippet.  A
    capability phrase can therefore retrieve a notice after character 2,000
    yet disappear before deterministic relevance sees it.  These compact
    contexts preserve the exact source text that caused retrieval without
    copying a multi-page notice into every sweep record.
    """
    evidence: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for term in keywords:
        pattern = _keyword_pattern(term)
        if pattern is None:
            continue
        for field, value in (("title", title), ("description", description)):
            match = pattern.search(value)
            if match is None:
                continue
            start, end = match.span()
            key = (field, term.casefold(), start)
            if key in seen:
                continue
            seen.add(key)
            lo = max(0, start - SCREEN_EVIDENCE_CONTEXT_CHARS)
            hi = min(len(value), end + SCREEN_EVIDENCE_CONTEXT_CHARS)
            evidence.append({
                "term": term,
                "field": field,
                "matched_text": match.group(0),
                "context": value[lo:hi],
                "start": start,
            })
            break
        if len(evidence) >= MAX_SCREEN_EVIDENCE_MATCHES:
            break
    return evidence


def _attachment_anchor_terms(taxonomy) -> set[str]:
    """Broad retrieval tokens only; they never count as relevance evidence."""
    return {
        token.casefold()
        for term in [*(getattr(taxonomy, "core", None) or []),
                     *(getattr(taxonomy, "adjacent", None) or [])]
        for token in re.findall(r"[A-Za-z0-9]+", str(term.term))
        if len(token) >= 4 and token.casefold() not in _ATTACHMENT_ANCHOR_STOP
    }


@register_source
class SamExtractSource(DataSource):
    name = "sam_extract"
    kind = SourceKind.DISCOVERY

    #: census of the last search(): the screen's ground truth, set per call.
    #: Failed selection/read never retains a previous successful census.
    last_census: dict = {}
    last_attachment_census: dict = {}

    def healthcheck(self) -> tuple[bool, str]:
        latest = _latest_path()
        if latest is not None:
            age_d = (date.today() - date.fromisoformat(
                latest.stem.replace("opportunities_", ""))).days
            return True, f"local extract on disk ({age_d}d old) — quota-free"
        try:
            with httpx.Client(timeout=10.0,
                              transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                              follow_redirects=True) as c:
                r = c.head(EXTRACT_URL)
                r.raise_for_status()
            return True, "extract reachable (no key, no quota)"
        except Exception as e:  # noqa: BLE001
            return False, f"extract unreachable: {e}"

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        """Screen EVERY active notice against every NAICS lane and keyword.

        This is the multi-keyword search the live API can't do: the API takes
        one NAICS or one title substring per (rationed) call — here the whole
        keyword strategy runs against the whole notice universe at once, free.
        """
        census = {"source": "sam_extract", "complete": False,
                  "extract_receipts": []}
        self.last_census = census
        try:
            return self._search(query, census)
        except Exception as exc:
            census.update(error_type=type(exc).__name__, error=str(exc)[:240])
            raise

    def _search(self, query: SourceQuery, census: dict) -> list[RawOpportunity]:
        path = _select_extract(census)
        naics = {c.strip() for c in (query.naics_codes or []) if c.strip()}
        kws = [
            k.strip().strip('"') for k in (query.keywords or []) if k.strip()
        ]
        keyword_patterns = [
            (term, pattern)
            for term in kws
            if (pattern := _keyword_pattern(term)) is not None
        ]
        # FILTER-FIRST (L18, operator decision 2026-07-10): when the gate
        # names focus agencies, the notice list that flows downstream (and
        # into paid triage) is the FOCUS SLICE; the whole-market numbers stay
        # in the census because this screen is free. A department focus
        # captures its components (CBP under DHS).
        from tools.agencies import find, matches_record
        focus = [a for a in (find(n) for n in (query.agencies or [])) if a]
        results: list[RawOpportunity] = []
        seen: set[str] = set()
        n_scanned = n_active = n_market = 0
        with _read_extract(path, census, "primary") as fh:
            for r in iter_rows(fh):
                n_scanned += 1
                if (r.get("active") or "").strip().lower() not in ("yes", "true", "y", ""):
                    continue
                n_active += 1
                deadline = _parse_dt(r.get("deadline", ""))
                if query.deadline_from and deadline and deadline < query.deadline_from:
                    continue  # dead inventory; keep None-deadline (RFIs/sources sought)
                hit_naics = bool(naics) and any(
                    (r.get("naics") or "").startswith(c) for c in naics)
                matched_keywords: list[str] = []
                if keyword_patterns:
                    # THE SCREEN READS THE WHOLE DESCRIPTION (2026-07-30).
                    # The old [:SCREEN_DESCRIPTION_CHARS] window covered
                    # 36.9% of description bytes and left 26.6% of notices
                    # only partially screened: a client keyword at char
                    # 2,500 was a silent miss on a live solicitation.
                    # Host-problem language sits deeper in a statement of
                    # work than a brand name does. The snippet handed
                    # downstream stays capped; the SCREEN does not.
                    hay = ((r.get("title") or "") + " "
                           + (r.get("description") or ""))
                    matched_keywords = [
                        term for term, pattern in keyword_patterns
                        if pattern.search(hay)
                    ]
                hit_kw = bool(matched_keywords)
                if not (hit_naics or hit_kw or (not naics and not kws)):
                    continue
                sid = r.get("notice_id") or ""
                if sid and sid in seen:
                    continue
                seen.add(sid)
                n_market += 1
                if focus:
                    agency_s = " ".join(x for x in (r.get("agency"),
                                                    r.get("subtier")) if x)
                    if not any(matches_record(agency_s, a) for a in focus):
                        continue  # market-counted, focus-excluded (free layer)
                opp = _to_opportunity(r)
                opp.raw_payload["matched"] = (
                    (["naics"] if hit_naics else [])
                    + matched_keywords[:MAX_SCREEN_EVIDENCE_MATCHES])
                evidence = _screen_evidence(
                    r.get("title") or "",
                    r.get("description") or "",
                    matched_keywords,
                )
                if evidence:
                    opp.raw_payload["screen_evidence_matches"] = evidence
                results.append(opp)
        census.update({
            "matched": len(results), "market_matched": n_market,
            "active_screened": n_active, "rows_scanned": n_scanned,
            "complete": True, "source": "sam_extract",
            "focus": [a["abbr"] for a in focus] or None,
        })
        return results

    def attachment_candidates(self, query: SourceQuery, taxonomy,
                              engagement_scope=None, *, limit: int = 8
                              ) -> list[RawOpportunity]:
        """Bounded near-match queue for requirements-file enrichment.

        This is deliberately broader than publication relevance and narrower
        than the active census. Two distinct client-vocabulary anchor tokens,
        positive engagement scope, and an actionable notice family are needed
        before any public attachment is downloaded. The sanctioned relevance
        scorer still decides whether extracted text may publish.
        """
        census = {"source": "sam_extract", "complete": False,
                  "extract_receipts": []}
        self.last_attachment_census = census
        try:
            return self._attachment_candidates(
                query, taxonomy, engagement_scope, limit=limit, census=census)
        except Exception as exc:
            census.update(error_type=type(exc).__name__, error=str(exc)[:240])
            raise

    def _attachment_candidates(self, query: SourceQuery, taxonomy,
                               engagement_scope, *, limit: int, census: dict
                               ) -> list[RawOpportunity]:
        if limit <= 0:
            census.update(eligible=0, threads=0, selected=0,
                          scan_skipped_reason="limit_zero")
            return []
        from tools.api.sam_notice_family import notice_family_rank
        from tools.agencies import find, matches_record
        from tools.relevance.engine import score_record

        anchors = _attachment_anchor_terms(taxonomy)
        if len(anchors) < 2:
            census.update(eligible=0, threads=0, selected=0,
                          scan_skipped_reason="insufficient_anchors")
            return []
        focus = [agency for agency in
                 (find(name) for name in (query.agencies or [])) if agency]
        path = _select_extract(census)
        latest_by_thread: dict[str, tuple[float, str]] = {}

        def active_thread(row: dict) -> tuple[Optional[int], str]:
            if (row.get("active") or "").strip().casefold() not in (
                    "yes", "true", "y", ""):
                return None, ""
            if focus:
                agency_text = " ".join(
                    value for value in (row.get("agency"), row.get("subtier"))
                    if value)
                if not any(matches_record(agency_text, agency)
                           for agency in focus):
                    return None, ""
            family = notice_family_rank({
                "title": row.get("title") or "",
                "raw_payload": {"type": row.get("type") or ""},
            })
            thread = re.sub(
                r"\s+", " ", str(row.get("solicitation")
                                   or row.get("notice_id") or "").strip()
            ).casefold()
            return family, thread

        def inside_window(row: dict) -> bool:
            posted = _parse_dt(row.get("posted", ""))
            deadline = _parse_dt(row.get("deadline", ""))
            return not (
                (query.posted_from and posted and posted < query.posted_from)
                or (query.posted_to and posted and posted > query.posted_to)
                or (query.deadline_from and deadline
                    and deadline < query.deadline_from)
                or (query.deadline_to and deadline
                    and deadline > query.deadline_to)
            )

        # Amendments are separate extract rows. Establish the latest active
        # record per solicitation thread first so an obsolete attachment set
        # can never win merely because its old response date was earlier.
        with _read_extract(path, census, "attachment_latest") as handle:
            for row in iter_rows(handle):
                family, thread = active_thread(row)
                if family is None or not thread:
                    continue
                source_id = str(row.get("notice_id") or "")
                recency = (_posted_order(row.get("posted", "")), source_id)
                if recency > latest_by_thread.get(thread, (-1, "")):
                    latest_by_thread[thread] = recency

        selected_by_thread: dict[str, tuple[tuple, RawOpportunity]] = {}
        eligible = 0
        with _read_extract(path, census, "attachment_candidates",
                           previous=census["extract_receipts"][0]) as handle:
            for row in iter_rows(handle):
                family, thread = active_thread(row)
                if family is None or not thread:
                    continue
                source_id = str(row.get("notice_id") or "")
                if source_id != latest_by_thread[thread][1]:
                    continue
                if not inside_window(row):
                    continue
                text = (
                    (row.get("title") or "") + " "
                    + (row.get("description") or "")   # full text (2026-07-30)
                ).casefold()
                # Tokenize once per row instead of running one regex per
                # taxonomy anchor across the entire 78k-notice census.
                hits = sorted(anchors.intersection(
                    re.findall(r"[a-z0-9]+", text)))
                if len(hits) < 2:
                    continue
                opportunity = _to_opportunity(row)
                verdict = score_record(
                    opportunity.model_dump(mode="python"), taxonomy,
                    engagement_scope=engagement_scope)
                if verdict.off_scope or "unresolved" in verdict.scope_basis \
                        or verdict.relevant:
                    continue
                eligible += 1
                opportunity.raw_payload["attachment_candidate_basis"] = {
                    "anchors": hits,
                    "notice_family_rank": family,
                    "scope_basis": verdict.scope_basis,
                    "outside_code_boundary": verdict.excluded_by_code,
                }
                deadline = _parse_dt(row.get("deadline", ""))
                posted = _parse_dt(row.get("posted", ""))
                rank = (
                    -len(hits),
                    bool(verdict.excluded_by_code),
                    family,
                    deadline.isoformat() if deadline else "9999-12-31",
                    -(posted.toordinal() if posted else 0),
                    opportunity.source_id,
                )
                selected_by_thread[thread] = (rank, opportunity)
        ranked = [item[1] for item in sorted(
            selected_by_thread.values(), key=lambda item: item[0])]
        chosen = ranked[:limit]
        census.update({
            "eligible": eligible,
            "threads": len(selected_by_thread),
            "selected": len(chosen),
            "focus": [agency["abbr"] for agency in focus] or None,
            "complete": True,
        })
        return chosen
