"""Bounded official program publications, independent of client fit or lead count.

Registry entries select publisher documents/sections, never sales dispositions.
Original bytes and extraction receipts survive parsing and network failures.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urljoin, urlsplit

from tools.api import _http
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.program_cache import ProgramPull, cached_program_pull
from tools.artifacts import atomic_write_json

SOURCE = "agency_program_documents"
VERSION = "agency-program-document.v2"
MDA_LANDING = "https://comptroller.war.gov/Budget-Materials/FY2027BudgetJustification/"
MDA_PDF = "https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/budget_justification/pdfs/03_RDT_and_E/RDTE_Vol2_MDA_RDTE_PB27_Justification_Book.pdf"
CBP_PDF = "https://www.dhs.gov/sites/default/files/2025-05/25_0528_cbp_biometric_entry_exit_h-1b_and_l-1_fees_spend_plan.pdf"
DOCUMENTS = (
    dict(id="mda-md30-pb2027", program_id="PE0603890C:MD30", agency="Missile Defense Agency",
         component="Missile Defense Agency", title="MD30 enterprise IT, FY2027 RDT&E justification",
         kind="budget", landing=MDA_LANDING, url=MDA_PDF, pages=list(range(296, 307)),
         markers=["MD30", "0603890C"], publisher_date="2026-04", date_marker="April 2026"),
    dict(id="cbp-biometric-plan-fy2024", program_id="CBP:Biometric-Entry-Exit",
         agency="Department of Homeland Security", component="U.S. Customs and Border Protection",
         title="Biometric Entry-Exit H-1B and L-1 Fees Spend Plan", kind="budget",
         landing=CBP_PDF, url=CBP_PDF, pages=[1, 2, 3, 4, 13], markers=["Biometric", "Spend Plan"],
         publisher_date="2025-03-10", date_marker="March 10, 2025"),
    dict(id="cbp-biometric-fee-2026-16231", program_id="CBP:Biometric-Entry-Exit",
         agency="Department of Homeland Security", component="U.S. Customs and Border Protection",
         title="Biometric fee final rule, 2026-16231", kind="regulation",
         landing="https://www.govinfo.gov/content/pkg/FR-2026-08-10/pdf/2026-16231.pdf",
         url="https://www.govinfo.gov/content/pkg/FR-2026-08-10/pdf/2026-16231.pdf", pages=[1, 2, 3],
         columns=[(40, 54, 216, 746), (216, 54, 393, 746), (393, 54, 580, 746)],
         section_start="DEPARTMENT OF HOMELAND SECURITY 8 CFR Part 106",
         markers=["Biometric", "Panetta"], publisher_date="2026-08-10", date_marker="August 10, 2026"),
    dict(id="cbp-biometric-pilot-20260901", program_id="CBP:Biometric-Entry-Exit",
         agency="Department of Homeland Security", component="U.S. Customs and Border Protection",
         title="Biometrics at the Border: Making Exit Secure and Efficient", kind="agency_announcement",
         landing="https://www.dhs.gov/science-and-technology/news/2026/09/01/feature-article-biometrics-border-making-exit-secure-and-efficient",
         url="https://www.dhs.gov/science-and-technology/news/2026/09/01/feature-article-biometrics-border-making-exit-secure-and-efficient",
         pages=None, markers=["Panetta", "biometric"], publisher_date="2026-09-01", date_marker="September 1, 2026"),
)


def _root():
    return Path(os.environ.get("LILA_PROGRAM_DOCUMENT_CACHE_DIR",
                Path(__file__).resolve().parents[2] / "data/cache/program_documents"))


def _official(url):
    p = urlsplit(url)
    return p.scheme == "https" and not p.username and not p.password and (p.hostname or "").endswith((".gov", ".mil"))


def _system_fetch(url, max_bytes):
    """Retry a certificate-store failure using system curl with TLS verified.

    Redirects are followed manually, only to official HTTPS origins. No -k,
    credentials, cookies, proxy changes, or retries on ordinary HTTP errors.
    """
    end = time.monotonic() + 90
    chain = []
    with tempfile.TemporaryDirectory(prefix='lila-program-tls-') as directory:
        body, headers = Path(directory) / 'body', Path(directory) / 'headers'
        for _ in range(6):
            if not _official(url):
                raise ValueError('system TLS redirect left official HTTPS origins')
            chain.append(url)
            remaining = max(1, int(end - time.monotonic()))
            completed = subprocess.run(['/usr/bin/curl', '--disable', '--silent', '--show-error',
                '--proto', '=https', '--max-time', str(remaining),
                '--max-filesize', str(max_bytes), '--dump-header', str(headers),
                '--output', str(body), url], capture_output=True, timeout=remaining + 2)
            if completed.returncode:
                raise RuntimeError('verified system TLS retrieval failed: ' + completed.stderr.decode(errors='replace')[:300])
            blocks = re.split(r'\r?\n\r?\n', headers.read_text())
            block = next((b for b in reversed(blocks) if b.startswith('HTTP/')), '')
            lines = block.splitlines()
            status = int(lines[0].split()[1])
            fields = {k.lower(): v.strip() for line in lines[1:] if ':' in line for k, v in [line.split(':', 1)]}
            if 300 <= status < 400 and fields.get('location'):
                url = urljoin(url, fields['location'])
                continue
            if status != 200:
                raise ValueError(f'system TLS HTTP status {status}')
            if body.stat().st_size > max_bytes:
                raise ValueError('system TLS capture exceeded byte boundary')
            return body.read_bytes(), dict(final_url=url, redirects=chain, http_status=status,
                                            content_type=fields.get('content-type'))
    raise ValueError('system TLS redirect boundary exceeded')


def fetch_document(url, *, root, max_bytes=32_000_000):
    """Shared verified TLS transport, bounded bytes/time, immutable byte custody."""
    if not _official(url):
        raise ValueError("program document requires an official HTTPS URL")
    started = datetime.now(timezone.utc).isoformat()
    receipt = dict(requested_url=url, retrieved_at=started, method="GET", attempts=[])
    try:
        try:
            with _http._client(45) as client:
                stop = time.monotonic() + 90
                with client.stream("GET", url) as response:
                    chain = [str(r.url) for r in response.history] + [str(response.url)]
                    if not all(_official(u) for u in chain):
                        raise ValueError("document redirect left official HTTPS origins")
                    receipt.update(final_url=str(response.url), redirects=chain,
                                   http_status=response.status_code,
                                   content_type=response.headers.get("content-type"))
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes(65536):
                        if len(data) + len(chunk) > max_bytes or time.monotonic() > stop:
                            raise ValueError("program capture exceeded byte/time boundary")
                        data.extend(chunk)
            blob = bytes(data)
            receipt['attempts'].append({'transport': 'httpx', 'state': 'success', 'tls_verified': True})
        except Exception as exc:
            receipt['attempts'].append({'transport': 'httpx', 'state': 'failed',
                                       'error_type': type(exc).__name__, 'error': str(exc)[:400]})
            if not re.search(r'certificate.verify.failed|certificate verify failed', str(exc), re.I):
                raise
            try:
                blob, resolved = _system_fetch(url, max_bytes)
                receipt.update(resolved)
                receipt['attempts'].append({'transport': 'system-curl', 'state': 'success', 'tls_verified': True})
            except Exception as retry:
                receipt['attempts'].append({'transport': 'system-curl', 'state': 'failed', 'error': str(retry)[:400]})
                raise
        sha = hashlib.sha256(blob).hexdigest()
        target = root / "bytes" / sha
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:
                with target.open("xb") as f:
                    f.write(blob)
            except FileExistsError:
                pass
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError("stored document bytes differ from capture")
        receipt.update(status="captured", raw_sha256=sha, byte_count=len(blob), byte_path=str(target))
        return blob, receipt
    except Exception as exc:
        receipt.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:400])
        raise
    finally:
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        key = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
        atomic_write_json(root / "attempts" / f"{key}.json", receipt)


def parse_document(spec, blob, receipt, landing_blob, landing_receipt):
    """Retain page text/table layout and date precision; never infer funding."""
    from tools.scrape.site import _extract
    if spec["landing"] != spec["url"]:
        _, hrefs = _extract(landing_blob.decode("utf-8", errors="replace"))
        links = {urljoin(landing_receipt["final_url"], href).split("#")[0] for href in hrefs}
        if spec["url"] not in links:
            raise ValueError("publisher landing page no longer links the registered document")
    pages = []
    if blob.startswith(b"%PDF"):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(blob)) as pdf:
            selected = spec["pages"] or list(range(1, len(pdf.pages) + 1))
            if len(selected) > 40 or not selected or max(selected) > len(pdf.pages):
                raise ValueError("document selection outside registered page boundary")
            total = len(pdf.pages)
            section_found = not spec.get("section_start")
            for page in selected:
                text = pdf.pages[page - 1].extract_text(layout=True) or ""
                if not text.strip():
                    raise ValueError(f"unreadable PDF page {page}; OCR not performed")
                reading = text
                if spec.get("columns"):
                    # Federal Register reading order is down each column, not
                    # across the page. Keep untouched layout beside this view.
                    reading = " ".join(re.sub(r"\s+", " ", pdf.pages[page - 1].crop(box).extract_text() or "").strip()
                                       for box in spec["columns"])
                    if not section_found:
                        start = reading.find(spec["section_start"])
                        if start < 0:
                            raise ValueError("registered rule section absent from first selected page")
                        reading = reading[start:]
                        section_found = True
                    header = pdf.pages[page - 1].crop((0, 0, 612, 54)).extract_text() or ""
                    reading = header + "\n" + re.sub(r"\s*@\s*", "@", reading)
                pages.append({"pdf_page": page, "text": text,
                              "sha256": hashlib.sha256(text.encode()).hexdigest(),
                              "reading_order_text": reading,
                              "reading_order_sha256": hashlib.sha256(reading.encode()).hexdigest()})
    else:
        text, _ = _extract(blob.decode("utf-8", errors="replace"))
        if not text or len(text) > 100_000:
            raise ValueError("HTML publication outside text boundary")
        total = None
        pages = [{"section": "main", "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}]
    # PDF line wraps are presentation, not sentence boundaries. Preserve full
    # layout in each page receipt, but unfold it for contextual phrase matching.
    description = "\n\n".join((f"PDF page {p['pdf_page']}\n" if 'pdf_page' in p else "")
                                  + re.sub(r"\s+", " ", p.get('reading_order_text', p['text'])).strip() for p in pages)
    collapsed = re.sub(r"\s+", " ", description).casefold()
    if any(m.casefold() not in collapsed for m in spec["markers"]):
        raise ValueError("registered program identity missing in selected document")
    if spec["date_marker"].casefold() not in collapsed:
        raise ValueError("publisher date is not supported by the captured document text")
    evidence = {"schema_version": VERSION, "publisher": spec["agency"],
                "landing": landing_receipt, "document": receipt, "pages": pages,
                "total_pdf_pages": total, "extraction_method": "pdfplumber layout with registered column order / HTML visible text",
                "selection": {k: spec[k] for k in ("pages", "columns", "section_start") if k in spec},
                "extraction_sha256": hashlib.sha256(description.encode()).hexdigest(),
                "publication_date": spec["publisher_date"],
                "publication_date_basis": spec["date_marker"],
                "date_precision": "month" if len(spec["publisher_date"]) == 7 else "day",
                "landing_kind": "document_endpoint" if spec["landing"] == spec["url"] else "publisher_index"}
    return {"record_id": "program-document:" + spec["id"], "source": SOURCE, "tier": "program",
            "kind": spec["kind"], "title": spec["title"], "agency": spec["agency"],
            "component": spec["component"], "program_id": spec["program_id"],
            "description": description, "canonical_url": receipt["final_url"],
            "retrieved_at": receipt["retrieved_at"], "data_as_of": spec["publisher_date"],
            "document_evidence": evidence,
            "funding_status": "not_established", "current_remaining_work": "not_established"}


def _matches_scope(spec, agencies):
    from tools.agencies import find
    if not agencies:
        return True
    names = {spec["agency"].casefold(), spec["component"].casefold()}
    component = find(spec["component"])
    for label in agencies:
        found = find(label)
        if label.casefold() in names or found and (found['name'].casefold() in names or
                component and found['abbr'] == component.get('parent')):
            return True
    return False


@register_source
class AgencyProgramDocumentsSource(DataSource):
    name = SOURCE
    kind = SourceKind.ENRICHMENT

    def search(self, query):
        raise NotImplementedError("PROGRAM enrichment only")

    def healthcheck(self):
        return True, "Bounded official document registry; live retrieval is individually receipted"

    def enrich(self, query: SourceQuery):
        from tools.toggles import is_enabled
        if not is_enabled(SOURCE, True):
            return {"records": [], "_provenance": {"status": "not-run", "mode": "disabled"}}
        records, attempts = [], []
        for spec in DOCUMENTS:
            if not _matches_scope(spec, query.agencies):
                continue
            root = _root() / spec["id"]

            def fetch(spec=spec, root=root):
                blob, rec = fetch_document(spec["url"], root=root)
                landing_blob, landing_rec = ((blob, rec) if spec["landing"] == spec["url"] else
                                             fetch_document(spec["landing"], root=root))
                row = parse_document(spec, blob, rec, landing_blob, landing_rec)
                return ProgramPull([row], spec["publisher_date"], source_attempts=[rec, landing_rec],
                                   limitations="Registered program sections only; dates and amounts are source context, not a buying decision")

            rows, receipt = cached_program_pull(source=SOURCE, canonical_url=spec["url"],
                                               cache_dir=root / "snapshots" / hashlib.sha256(json.dumps([VERSION, spec], sort_keys=True).encode()).hexdigest(), fetch_live=fetch)
            try:
                for row in rows:
                    ev = row['document_evidence']
                    for captured in (ev['document'], ev['landing']):
                        path = root / 'bytes' / captured['raw_sha256']
                        if hashlib.sha256(path.read_bytes()).hexdigest() != captured['raw_sha256']:
                            raise ValueError('cached original document custody differs')
                    if hashlib.sha256(row['description'].encode()).hexdigest() != ev['extraction_sha256']:
                        raise ValueError('cached document extraction differs')
            except (OSError, KeyError, ValueError) as exc:
                rows = []
                receipt = {**receipt, 'status': 'failed', 'custody_error': str(exc)}
            records.extend(rows)
            attempts.append({"document_id": spec["id"], "agency": spec["agency"],
                             "component": spec["component"], **receipt})
        selected = records[:query.limit]
        return {"records": selected, "source_attempts": attempts, "_provenance": {
            "source": SOURCE, "status": "partial" if any(a['status'] != 'success' for a in attempts) else "success",
            "records_received": len(records), "candidate_total": len(records), "selected_count": len(selected),
            "truncated": len(selected) < len(records), "selection_order": "registered document identity",
            "scope": list(query.agencies), "coverage_boundary": "Registered MDA MD30 and CBP biometric publications only"}}
