"""Bounded text extraction for public SAM.gov solicitation attachments.

Descriptions establish breadth; an RFP/PWS/SOW often carries the actual
capability language. This module follows only SAM's constructed public-file
route, caps bytes and extracted text, and permanently caches the result by
resource ID. No LLM participates in retrieval or extraction.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from tools.api.sam_gov import SAM_HEADERS
from tools.api.sam_notice_detail import RESOURCE_DOWNLOAD_URL_TPL, attachment_withdrawn

MAX_ATTACHMENT_BYTES = int(os.environ.get(
    "LILA_SAM_ATTACHMENT_MAX_BYTES", str(5 * 1024 * 1024)))
MAX_ATTACHMENT_TEXT_CHARS = int(os.environ.get(
    "LILA_SAM_ATTACHMENT_TEXT_CHARS", "50000"))
MAX_PDF_PAGES = int(os.environ.get("LILA_SAM_ATTACHMENT_PDF_PAGES", "100"))
MAX_PDF_PARSE_SECONDS = int(os.environ.get(
    "LILA_SAM_ATTACHMENT_PDF_PARSE_SECONDS", "20"))
MAX_DOCX_XML_BYTES = int(os.environ.get(
    "LILA_SAM_ATTACHMENT_DOCX_XML_BYTES", str(20 * 1024 * 1024)))
_DEFAULT_CACHE = (
    Path(__file__).resolve().parents[2] / "data" / "cache"
    / "sam_attachment_text"
)
_RESOURCE_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_ALLOWED_FINAL_HOSTS = {
    "sam.gov",
    "iae-fbo-attachments.s3.amazonaws.com",
}
_TEXT_EXTENSIONS = {".txt", ".csv", ".md"}


def _cache_dir() -> Path:
    return Path(os.environ.get(
        "LILA_SAM_ATTACHMENT_TEXT_DIR", str(_DEFAULT_CACHE)))


def _public_file(attachment: dict) -> tuple[bool, str]:
    if attachment_withdrawn(attachment):
        return False, 'withdrawn attachment'
    resource_id = str(attachment.get("resource_id") or "").strip()
    if not _RESOURCE_ID.fullmatch(resource_id):
        return False, "invalid resource id"
    for key in ("access_level", "access_status"):
        value = str(attachment.get(key) or "").strip().casefold()
        if value and value != "public":
            return False, f"{key} is not public"
    open_flags = {"", "0", "false", "no", "n"}
    if str(attachment.get("export_controlled") or "").strip().casefold() \
            not in open_flags:
        return False, "export-controlled attachment"
    if str(attachment.get("explicit_access") or "").strip().casefold() \
            not in open_flags:
        return False, "explicit-access attachment"
    try:
        size = int(attachment.get("size") or 0)
    except (TypeError, ValueError):
        return False, "invalid attachment size"
    if size > MAX_ATTACHMENT_BYTES:
        return False, f"attachment exceeds {MAX_ATTACHMENT_BYTES} bytes"
    return True, "public"


def _unfetched_file_status(reason: str) -> str:
    return ('identity_mismatch' if reason == 'invalid resource id' else
            'not_fetched' if reason == 'withdrawn attachment' or 'exceeds' in reason
            or reason == 'invalid attachment size' else 'inaccessible')


def _remaining_seconds(deadline_monotonic: float | None,
                       default: float) -> float:
    if deadline_monotonic is None:
        return default
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("SAM attachment stage deadline reached")
    return min(default, remaining)


def _download_bytes(resource_id: str, *,
                    deadline_monotonic: float | None = None) -> bytes:
    url = RESOURCE_DOWNLOAD_URL_TPL.format(resource_id=resource_id)
    timeout = httpx.Timeout(max(
        0.25, _remaining_seconds(deadline_monotonic, 30.0)))
    transport = httpx.HTTPTransport(retries=0, local_address="0.0.0.0")
    with httpx.Client(timeout=timeout, transport=transport,
                      follow_redirects=True) as client:
        with client.stream("GET", url, headers=SAM_HEADERS) as response:
            response.raise_for_status()
            host = (urlparse(str(response.url)).hostname or "").casefold()
            if host not in _ALLOWED_FINAL_HOSTS:
                raise RuntimeError(
                    f"SAM attachment redirected to unapproved host {host!r}")
            declared = int(response.headers.get("content-length") or 0)
            if declared > MAX_ATTACHMENT_BYTES:
                raise RuntimeError(
                    f"SAM attachment declares {declared} bytes; cap is "
                    f"{MAX_ATTACHMENT_BYTES}")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(1 << 16):
                _remaining_seconds(deadline_monotonic, 30.0)
                total += len(chunk)
                if total > MAX_ATTACHMENT_BYTES:
                    raise RuntimeError(
                        f"SAM attachment exceeded {MAX_ATTACHMENT_BYTES} bytes")
                chunks.append(chunk)
    return b"".join(chunks)


def _pdf_text_local(payload: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(payload)) as document:
        parts: list[str] = []
        length = 0
        for page in document.pages[:MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            parts.append(text)
            length += len(text)
            if length >= MAX_ATTACHMENT_TEXT_CHARS:
                break
        return "\n".join(parts)


def _pdf_text(payload: bytes, *,
              deadline_monotonic: float | None = None) -> str:
    """Extract PDF text in a killable child process.

    pdfplumber materializes the document page tree before slicing. Keeping
    that parser out of the sweep process makes the page and time limits real
    even for a pathological public upload.
    """
    timeout = max(0.25, _remaining_seconds(
        deadline_monotonic, float(MAX_PDF_PARSE_SECONDS)))
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "tools.api.sam_attachment_text",
             "--extract-pdf"],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[2]),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"PDF text extraction exceeded {timeout:.1f}s") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[:200]
        raise ValueError(f"PDF text extraction failed: {detail}")
    return completed.stdout.decode("utf-8", errors="replace")


def _docx_text(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member = archive.getinfo("word/document.xml")
        if member.file_size > MAX_DOCX_XML_BYTES:
            raise ValueError(
                f"DOCX document XML exceeds {MAX_DOCX_XML_BYTES} bytes")
        root = ElementTree.fromstring(archive.read(member))
    return " ".join(
        node.text or "" for node in root.iter()
        if node.tag.endswith("}t") and (node.text or "").strip()
    )


def _text_from_bytes(name: str, payload: bytes, *,
                     deadline_monotonic: float | None = None) -> str:
    _remaining_seconds(deadline_monotonic, 30.0)
    suffix = Path(name).suffix.casefold()
    if suffix == ".pdf":
        text = _pdf_text(payload, deadline_monotonic=deadline_monotonic)
    elif suffix == ".docx":
        text = _docx_text(payload)
    elif suffix in _TEXT_EXTENSIONS:
        text = payload.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"unsupported public attachment type {suffix or 'unknown'}")
    _remaining_seconds(deadline_monotonic, 30.0)
    return re.sub(r"[ \t]{2,}", " ", text).strip()[
        :MAX_ATTACHMENT_TEXT_CHARS]


def extract_public_attachment_text(
        attachment: dict, *, deadline_monotonic: float | None = None) -> dict:
    """Download/extract one public attachment; errors are named, never fatal."""
    resource_id = str(attachment.get("resource_id") or "").strip()
    name = str(attachment.get("name") or resource_id)
    allowed, reason = _public_file(attachment)
    if not allowed:
        status = _unfetched_file_status(reason)
        return {"resource_id": resource_id, "name": name, "text": "",
                "sha256": "", "error": reason, "from_cache": False,
                "collection_status": status, "authority": "discovery_only"}
    binding = hashlib.sha256(json.dumps(
        {k: attachment.get(k) for k in (
            "resource_id", "name", "type", "source_url", "size",
            "access_level", "access_status", "export_controlled", "explicit_access",
            "deleted_flag", "deleted_date", "file_exists")},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cache = _cache_dir() / f"{resource_id}.json"
    if cache.exists():
        try:
            result = json.loads(cache.read_text())
            if _text_capture_valid(result, resource_id, binding):
                result["from_cache"] = True
                return result
        except (OSError, ValueError, TypeError):
            pass
    result = {"resource_id": resource_id, "name": name, "text": "",
              "sha256": "", "error": "", "from_cache": False,
              "collection_status": "lookup_failed", "authority": "discovery_only",
              "attachment_binding_sha256": binding,
              "source_url": RESOURCE_DOWNLOAD_URL_TPL.format(resource_id=resource_id),
              "request_started_at": datetime.now(timezone.utc).isoformat()}
    acquired = False
    retention_errors = []
    try:
        from tools.api.sam_capture import retain_bytes
        payload = _download_bytes(
            resource_id, deadline_monotonic=deadline_monotonic)
        acquired = True
        result.update(
            sha256=hashlib.sha256(payload).hexdigest(),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            collection_status="unreadable")
        try:
            result['raw_capture'] = retain_bytes(_cache_dir(), payload)
        except (OSError, ValueError) as exc:
            retention_errors.append(str(exc)[:300])
        text = _text_from_bytes(
            name, payload, deadline_monotonic=deadline_monotonic)
        if not text:
            raise ValueError("attachment yielded no readable text")
        # Extractor output is not necessarily valid Unicode. Encoding failure
        # is an unreadable source, not a local storage failure.
        text_bytes = text.encode('utf-8')
        try:
            result['text_capture'] = retain_bytes(_cache_dir(), text_bytes)
        except (OSError, ValueError) as exc:
            retention_errors.append(str(exc)[:300])
        if retention_errors:
            result.update(collection_status='retention_failed', diagnostic_text=text[:1000],
                          diagnostic_text_truncated=len(text) > 1000,
                          retention_errors=retention_errors,
                          error='local retention failed: ' + '; '.join(retention_errors))
            return result
        result.update(
            text=text,
            text_sha256=hashlib.sha256(text_bytes).hexdigest(),
            text_locator={"surface": "extracted_text", "unit": "unicode_characters",
                          "start": 0, "end": len(text)},
            extraction_limits={"max_text_characters": MAX_ATTACHMENT_TEXT_CHARS,
                               "max_pdf_pages": MAX_PDF_PAGES,
                               "completeness": "bounded_extraction_not_full_document_proof"},
            collection_status="captured_discovery_only")
        _cache_dir().mkdir(parents=True, exist_ok=True)
        from tools.artifacts import atomic_write_json
        try:
            atomic_write_json(cache, result)
        except OSError as exc:
            # The two source objects and returned receipt still establish
            # custody. Failure of this optional cache index does not undo it.
            result['cache_write_error'] = str(exc)[:300]
        result["from_cache"] = False
        return result
    except Exception as exc:  # noqa: BLE001 - one file never sinks a sweep
        result.update(text="", error=str(exc).encode('utf-8', errors='backslashreplace').decode()[:300])
        if isinstance(exc, UnicodeError):
            result.update(collection_status='unreadable', stop_reason='unencodable_extracted_text')
            if retention_errors:
                result['retention_errors'] = retention_errors
        elif retention_errors:
            result.update(collection_status='retention_failed', retention_errors=retention_errors)
        elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            stage_expired = (deadline_monotonic is not None
                             and time.monotonic() >= deadline_monotonic)
            result.update(collection_status='not_fetched',
                          stop_reason='stage_deadline' if stage_expired else
                          'extraction_deadline' if acquired else 'download_deadline',
                          stopped_phase='extraction' if acquired else 'download')
        elif getattr(getattr(exc, "response", None), "status_code", None) in (401, 403):
            result["collection_status"] = "inaccessible"
        return result


def _text_capture_valid(result: dict, resource_id: str, binding: str) -> bool:
    from tools.api.sam_capture import read_retained
    if not isinstance(result, dict):
        return False
    try:
        if (result.get("resource_id") != resource_id
                or result.get("attachment_binding_sha256") != binding
                or result.get("collection_status") != "captured_discovery_only"
                or result.get("authority") != "discovery_only"
                or result.get("source_url") != RESOURCE_DOWNLOAD_URL_TPL.format(resource_id=resource_id)):
            return False
        raw = read_retained(_cache_dir(), result["raw_capture"])
        text_bytes = read_retained(_cache_dir(), result["text_capture"])
        text = text_bytes.decode("utf-8")
        clock = datetime.fromisoformat(result["retrieved_at"].replace("Z", "+00:00"))
        return (clock.utcoffset() is not None and clock <= datetime.now(timezone.utc)
                and result.get("text") == text and bool(text.strip())
                and len(text) <= MAX_ATTACHMENT_TEXT_CHARS
                and result.get("sha256") == hashlib.sha256(raw).hexdigest()
                and result.get("text_sha256") == hashlib.sha256(text_bytes).hexdigest()
                and result.get("text_locator") == {
                    "surface": "extracted_text", "unit": "unicode_characters",
                    "start": 0, "end": len(text)})
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return False


def attachment_priority(attachment: dict) -> tuple:
    """Requirements-bearing files first; stable resource ID breaks ties."""
    name = str(attachment.get("name") or "").casefold()
    preferred = (
        "statement of work", "sow", "performance work statement", "pws",
        "requirements", "specification", "draft rfp", "rfi",
        "capability statement", "industry feedback",
    )
    rank = next((index for index, term in enumerate(preferred)
                 if term in name), len(preferred))
    return rank, name, str(attachment.get("resource_id") or "")


def _main() -> int:
    if sys.argv[1:] != ["--extract-pdf"]:
        return 2
    payload = sys.stdin.buffer.read(MAX_ATTACHMENT_BYTES + 1)
    if len(payload) > MAX_ATTACHMENT_BYTES:
        print("PDF exceeds byte cap", file=sys.stderr)
        return 2
    sys.stdout.write(_pdf_text_local(payload)[:MAX_ATTACHMENT_TEXT_CHARS])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
