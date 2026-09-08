"""Bound-entity SEC customer roster (S-1 / 424B4 / 10-K).

Identity-bound only. Resolves CIK from SEC company_tickers.json by the
bound name, fetches S-1 / 424B4 / 10-K text, and promotes named orgs
only from customer-roster sentences. Underwriter and cover-page tables
never promote. Soft-fail if SEC is unreachable. Evidence URL is the
filing. Prefer a filing that still contains the named roster when the
latest 10-K dropped names.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable, Optional
from agents.intake.extract import (
    customer_window_is_underwriter,
    is_customer_category_phrase,
    is_customer_name,
    is_customer_roster_excerpt,
    known_customers_in_text,
    recall_customers,
)
from tools.api._http import get_json, get_text
from tools.api.sec_edgar import HEADERS

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
FILING_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}"
)
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

CUSTOMER_FORMS = ("424B4", "S-1", "S-1/A", "10-K")
IPO_FORMS = ("424B4", "S-1", "S-1/A")
_FORM_RANK = {"424B4": 0, "S-1": 1, "S-1/A": 2, "10-K": 3}
_MAX_FILINGS = 16
_MAX_OLDER_FILES = 8
_BROWSE_ATOM = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
    "&CIK={cik}&type={form}&count=20&output=atom&owner=include"
)
_ATOM_INDEX = re.compile(
    r"/Archives/edgar/data/\d+/(\d+)/(\d{10}-\d{2}-\d{6})-index\.htm",
    re.I,
)
_SUBMISSION_FILENAME = re.compile(
    r"<FILENAME>\s*([^\s<]+\.htm)",
    re.I,
)

_LEGAL_TAIL = re.compile(
    r"\b(inc|incorporated|corp|corporation|ltd|llc|co|the|company|plc)\b",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def normalize_cik(cik: str | int | None) -> str:
    digits = re.sub(r"\D", "", str(cik or ""))
    if not digits:
        return ""
    return f"{int(digits):010d}"


def cik_int(cik: str | int | None) -> str:
    padded = normalize_cik(cik)
    return str(int(padded)) if padded else ""


def _norm_issuer(name: str) -> str:
    raw = _LEGAL_TAIL.sub(" ", (name or "").casefold())
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    return " ".join(raw.split())


def is_bound_sec_filing_url(url: str, cik: str | int | None = None) -> bool:
    """True for an EDGAR archives URL, optionally restricted to the bound CIK."""
    if not url:
        return False
    host = url.casefold()
    if "sec.gov" not in host or "/archives/edgar/data/" not in host:
        return False
    if cik in (None, ""):
        return True
    stem = cik_int(cik)
    if not stem:
        return False
    return f"/archives/edgar/data/{stem}/" in host


def filing_document_url(
    cik: str | int, accession: str, primary_document: str = "",
) -> str:
    acc = (accession or "").replace("-", "")
    doc = (primary_document or "").lstrip("/")
    if not doc:
        dashed = accession if "-" in (accession or "") else ""
        doc = f"{dashed}.txt" if dashed else f"{acc}.txt"
    return FILING_URL.format(cik_int=cik_int(cik), acc=acc, doc=doc)


def filing_text(raw: str) -> str:
    """Strip HTML/XML so roster sentences are plain text."""
    blob = raw or ""
    blob = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", blob)
    blob = re.sub(r"(?is)<br\s*/?>", ". ", blob)
    blob = re.sub(r"(?is)</p>", ". ", blob)
    blob = re.sub(r"(?is)<[^>]+>", " ", blob)
    blob = html.unescape(blob)
    return re.sub(r"\s+", " ", blob).strip()


def is_underwriter_sentence(text: str) -> bool:
    raw = text or ""
    if is_customer_roster_excerpt(raw):
        return False
    return bool(re.search(
        r"\b(book-running|underwriters?|co-managers?|prospectus)\b",
        raw, re.I,
    ))


def roster_sentences(text: str) -> list[str]:
    raw = filing_text(text)
    if not raw:
        return []
    out: list[str] = []
    for sent in _SENTENCE.split(raw):
        piece = sent.strip()
        if not piece or is_underwriter_sentence(piece):
            continue
        if is_customer_roster_excerpt(piece):
            out.append(piece)
    return out


def extract_roster_customers(
    text: str, client_name: str = "",
) -> list[tuple[str, str]]:
    """Return (name, roster-sentence excerpt) pairs. Underwriter tables drop."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sent in roster_sentences(text):
        names = recall_customers(sent, client_name) + known_customers_in_text(
            sent, client_name)
        for name in names:
            if not is_customer_name(name, client_name=client_name):
                continue
            if is_customer_category_phrase(name):
                continue
            if customer_window_is_underwriter(sent, name):
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append((name, sent[:400]))
    return found


def resolve_bound_cik(
    bound_name: str,
    *,
    tickers: dict | list | None = None,
    fetch_json: Optional[Callable[..., Any]] = None,
) -> Optional[str]:
    """Map the bound issuer name to a 10-digit CIK. None if unresolved."""
    target = _norm_issuer(bound_name)
    if not target:
        return None
    data = tickers
    if data is None:
        fetch = fetch_json or get_json
        data = fetch(TICKERS_URL, headers=HEADERS, timeout=8.0, retries=1)
    rows = data.values() if isinstance(data, dict) else (data or [])
    tokens = target.split()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _norm_issuer(str(row.get("title") or ""))
        if not title:
            continue
        if title == target or title.startswith(target + " ") or (
                len(tokens) >= 2 and target.startswith(title)):
            cik = row.get("cik_str")
            if cik is not None:
                return normalize_cik(cik)
    return None


def _rows_from_submissions(payload: dict) -> list[tuple[str, str, str]]:
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accs = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    rows: list[tuple[str, str, str]] = []
    for form, acc, doc in zip(forms, accs, docs):
        if form in CUSTOMER_FORMS and acc:
            rows.append((str(form), str(acc), str(doc or "")))
    return rows


def _form_sort_key(row: tuple[str, str, str]) -> tuple[int, str]:
    form, acc, _doc = row
    rank = _FORM_RANK.get(form, 9)
    # Prefer older 424B4 / S-1 (IPO roster) over the latest nameless 10-K.
    if form in IPO_FORMS:
        return (rank, acc)
    return (rank, "~")


def filing_has_named_roster(text: str, client_name: str = "") -> bool:
    """True only when a roster sentence names a Title-Case buying org."""
    return bool(extract_roster_customers(text, client_name))


def _primary_from_submission(raw: str) -> str:
    match = _SUBMISSION_FILENAME.search(raw or "")
    return match.group(1).strip() if match else ""


def _filings_from_atom(xml: str, form: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for match in _ATOM_INDEX.finditer(xml or ""):
        acc = match.group(2)
        if acc in seen:
            continue
        seen.add(acc)
        rows.append((form, acc, ""))
    return rows


def browse_form_filings(
    cik: str,
    form: str,
    *,
    fetch_text: Optional[Callable[..., str]] = None,
) -> list[tuple[str, str, str]]:
    """EDGAR company browse atom, used when submissions.recent dropped IPOs."""
    gett = fetch_text or get_text
    url = _BROWSE_ATOM.format(cik=normalize_cik(cik), form=form)
    try:
        xml = gett(url, headers=HEADERS, timeout=8.0, retries=1)
    except Exception:  # noqa: BLE001
        return []
    return _filings_from_atom(xml, form)


def list_customer_filings(payload: dict) -> list[tuple[str, str, str]]:
    rows = _rows_from_submissions(payload)
    rows.sort(key=_form_sort_key)
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for row in rows:
        if row[1] in seen:
            continue
        seen.add(row[1])
        out.append(row)
    return out


def _older_file_priority(item: dict) -> tuple[int, str]:
    """Prefer year files that cover the IPO window (2014)."""
    start = str(item.get("filingFrom") or "")
    covers_ipo = 0 if start.startswith("2014") or start.startswith("2015") else 1
    return (covers_ipo, start)


def _load_older_submissions(
    payload: dict,
    *,
    fetch_json: Callable[..., Any],
) -> list[tuple[str, str, str]]:
    extra = [
        item for item in ((payload.get("filings") or {}).get("files") or [])
        if isinstance(item, dict) and "submissions" in str(item.get("name") or "")
    ]
    extra.sort(key=_older_file_priority)
    rows: list[tuple[str, str, str]] = []
    for item in extra[:_MAX_OLDER_FILES]:
        name = str(item.get("name") or "")
        url = f"https://data.sec.gov/submissions/{name}"
        try:
            older = fetch_json(url, headers=HEADERS, timeout=8.0, retries=1)
        except Exception:  # noqa: BLE001 - older year file is optional
            continue
        rows.extend(_rows_from_submissions(older))
    return rows


def _fts_filings(
    bound_name: str,
    cik: str,
    *,
    fetch_json: Callable[..., Any],
) -> list[tuple[str, str, str]]:
    try:
        payload = fetch_json(
            FTS_URL,
            params={
                "q": f'"{bound_name}"',
                "forms": "424B4,S-1,S-1/A",
                "ciks": cik_int(cik),
            },
            headers=HEADERS,
            timeout=8.0,
            retries=1,
        )
    except Exception:  # noqa: BLE001
        return []
    rows: list[tuple[str, str, str]] = []
    for hit in ((payload.get("hits") or {}).get("hits") or []):
        src = hit.get("_source") or {}
        forms = src.get("root_forms") or src.get("file_type") or ""
        form = forms[0] if isinstance(forms, list) and forms else str(forms)
        acc = src.get("adsh") or src.get("accession_number") or ""
        doc = src.get("file") or src.get("primary_doc") or ""
        entity = str(src.get("entity_id") or src.get("cik") or "")
        if entity and cik_int(entity) != cik_int(cik):
            continue
        if form not in CUSTOMER_FORMS or not acc:
            continue
        rows.append((str(form), str(acc), str(doc)))
    return rows


def fetch_bound_sec_customers(
    bound_name: str,
    *,
    client_name: str = "",
    fetch_json: Optional[Callable[..., Any]] = None,
    fetch_text: Optional[Callable[..., str]] = None,
    tickers: dict | list | None = None,
) -> list[dict[str, str]]:
    """Soft-fail bound-CIK roster. Empty when SEC is down or CIK is unknown."""
    name = (bound_name or client_name or "").strip()
    if not name:
        return []
    getj = fetch_json or get_json
    gett = fetch_text or get_text
    try:
        cik = resolve_bound_cik(name, tickers=tickers, fetch_json=getj)
        if not cik:
            return []
        submissions = getj(
            SUBMISSIONS_URL.format(cik10=cik),
            headers=HEADERS, timeout=8.0, retries=1,
        )
        collected: list[tuple[str, str, str]] = list_customer_filings(submissions)
        collected.extend(_load_older_submissions(submissions, fetch_json=getj))
        for ipo_form in IPO_FORMS:
            collected.extend(browse_form_filings(cik, ipo_form, fetch_text=gett))
        collected.extend(_fts_filings(name, cik, fetch_json=getj))
        rows = list_customer_filings({"filings": {"recent": {
            "form": [r[0] for r in collected],
            "accessionNumber": [r[1] for r in collected],
            "primaryDocument": [r[2] for r in collected],
        }}})
        hits: list[dict[str, str]] = []
        seen: set[str] = set()
        for form, acc, doc in rows[:_MAX_FILINGS]:
            url = filing_document_url(cik, acc, doc)
            if not is_bound_sec_filing_url(url, cik):
                continue
            raw = ""
            try:
                raw = gett(url, headers=HEADERS, timeout=12.0, retries=1)
            except Exception:  # noqa: BLE001 - try the complete submission txt
                txt_url = filing_document_url(cik, acc, "")
                if txt_url == url:
                    continue
                try:
                    raw = gett(txt_url, headers=HEADERS, timeout=12.0, retries=1)
                    url = txt_url
                except Exception:  # noqa: BLE001
                    continue
            if not filing_has_named_roster(raw, client_name or name):
                # Latest 10-K customer section is often category-only.
                continue
            primary = doc or _primary_from_submission(raw)
            if primary:
                html_url = filing_document_url(cik, acc, primary)
                if is_bound_sec_filing_url(html_url, cik):
                    url = html_url
            for org, excerpt in extract_roster_customers(
                    raw, client_name or name):
                key = org.casefold()
                if key in seen:
                    continue
                seen.add(key)
                hits.append({
                    "name": org,
                    "excerpt": excerpt,
                    "url": url,
                    "cik": cik,
                    "form": form,
                })
            # First filing that actually names orgs wins. IPO forms are first.
            if hits:
                break
        return hits
    except Exception:  # noqa: BLE001 - live SEC is optional context
        return []
