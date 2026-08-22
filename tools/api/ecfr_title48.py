"""eCFR Title 48 exact-citation adapter (keyless, official).

Client dimension: regulatory and requirements context.  This source resolves
FAR and agency-supplement citations found in notices or attachments to the
currently codified, point-in-time text.  It is distinct from Federal Register
notices, which describe rulemaking events rather than the codified requirement.

The eCFR API is not a general full-text search service.  The adapter therefore
accepts exact section citations only, retrieves the official Title metadata to
select an available version date, and performs at most eight section reads.
It never downloads the 20MB Title 48 XML or guesses a citation from prose.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from tools.api._http import get_json, get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.provenance import make_provenance_envelope

SOURCE_NAME = "ecfr_title48"
TITLES_URL = "https://www.ecfr.gov/api/versioner/v1/titles.json"
FULL_URL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-48.xml"
BROWSE_URL = "https://www.ecfr.gov/current/title-48"
MAX_CITATIONS = 8
MAX_TEXT_CHARS = 20_000

_LABELED_CITATION = re.compile(
    r"\b(?:48\s+CFR(?:\s+(?:section|sec\.?))?\s*|"
    r"FAR\s+|DFARS\s+|GSAR\s+|HHSAR\s+|DEAR\s+)"
    r"(?:§+\s*)?(\d{1,3}\.\d+(?:-\d+)?)\b",
    re.IGNORECASE,
)
_NAKED_CITATION = re.compile(r"(?<![\d.])(\d{1,3}\.\d{3,}(?:-\d+)?)(?![\d.])")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _citations(query: SourceQuery) -> tuple[list[tuple[str, list[str]]], bool]:
    by_citation: dict[str, list[str]] = {}
    for value in query.keywords:
        original = " ".join(str(value or "").strip().strip('"').split())
        if not original:
            continue
        matches = _LABELED_CITATION.findall(original)
        if not matches:
            matches = _NAKED_CITATION.findall(original)
        for citation in matches:
            terms = by_citation.setdefault(citation, [])
            if original not in terms:
                terms.append(original)
    ordered = sorted(by_citation.items(), key=lambda item: (
        [int(part) if part.isdigit() else part
         for part in re.split(r"([0-9]+)", item[0])],
    ))
    return ordered[:MAX_CITATIONS], len(ordered) > MAX_CITATIONS


def _title48(payload: Any) -> Optional[dict]:
    titles = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(titles, list):
        return None
    for row in titles:
        if isinstance(row, dict) and str(row.get("number")) == "48":
            return row
    return None


def _metadata(root: ET.Element) -> dict:
    raw = unescape(str(root.attrib.get("hierarchy_metadata") or ""))
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _section(xml_text: str, *, citation: str, version_date: str,
             matched_terms: list[str]) -> dict:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"eCFR returned malformed XML: {exc}") from exc
    identifier = str(root.attrib.get("N") or "").strip()
    if root.attrib.get("TYPE") != "SECTION" or identifier != citation:
        raise ValueError(
            f"eCFR section schema drift: requested {citation}, got {identifier or root.tag}")
    head = next((" ".join("".join(node.itertext()).split())
                 for node in root.iter("HEAD")), "")
    body = " ".join(" ".join(root.itertext()).split())[:MAX_TEXT_CHARS]
    meta = _metadata(root)
    alternate = str(meta.get("alternate_reference") or "").strip() or None
    canonical_citation = str(meta.get("citation") or "").strip() or f"48 CFR {citation}"
    return {
        "record_id": f"ecfr:48:{citation}",
        "title": 48,
        "section": citation,
        "heading": head or None,
        "citation": canonical_citation,
        "alternate_reference": alternate,
        "text": body or None,
        "version_date": version_date,
        "matched_terms": matched_terms,
        "url": f"https://www.ecfr.gov/on/{version_date}/title-48/section-{citation}",
    }


class EcfrTitle48Source(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(
        self,
        *,
        fetch_json: Optional[Callable[..., Any]] = None,
        fetch_text: Optional[Callable[..., str]] = None,
    ) -> None:
        self._fetch_json = fetch_json or get_json
        self._fetch_text = fetch_text or get_text

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use regulations()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            row = _title48(
                self._fetch_json(TITLES_URL, timeout=8.0, retries=1))
        except Exception as exc:  # noqa: BLE001
            return False, f"eCFR title metadata unreachable: {exc}"
        if not row or not row.get("up_to_date_as_of"):
            return False, "eCFR metadata has no Title 48 version date"
        return True, f"Title 48 up to date as of {row['up_to_date_as_of']}"

    def regulations(self, query: SourceQuery) -> dict[str, Any]:
        citations, citations_capped = _citations(query)
        if not citations:
            return self._failure(
                "no exact Title 48 section citation in query; free-text corpus scan refused",
                status="partial",
            )
        retrieved_at = _utc_now()
        try:
            title = _title48(
                self._fetch_json(TITLES_URL, timeout=10.0, retries=2))
            if not title or not title.get("up_to_date_as_of"):
                raise ValueError("eCFR metadata has no available Title 48 date")
            latest = str(title["up_to_date_as_of"])
            requested = query.posted_to.isoformat() if query.posted_to else None
            version_date = min(requested, latest) if requested else latest
        except Exception as exc:  # noqa: BLE001
            return self._failure(str(exc), retrieved_at=retrieved_at)

        rows: list[dict] = []
        attempts: list[dict] = []
        for citation, matched_terms in citations:
            url = FULL_URL.format(date=version_date) + "?" + urlencode(
                {"section": citation})
            try:
                xml_text = self._fetch_text(url, timeout=20.0, retries=2)
                rows.append(_section(
                    xml_text, citation=citation, version_date=version_date,
                    matched_terms=matched_terms))
                attempts.append({
                    "source": f"48 CFR {citation}",
                    "status": "success", "count": 1,
                })
            except Exception as exc:  # noqa: BLE001 - per-citation isolation
                attempts.append({
                    "source": f"48 CFR {citation}",
                    "status": "failed", "count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        failures = sum(row["status"] == "failed" for row in attempts)
        status = (
            "failed" if failures == len(attempts)
            else "partial" if failures or citations_capped
            else "complete"
        )
        limitations = [
            "exact-section resolver only; eCFR offers no bounded Title 48 full-text search API",
            "codified text is regulatory context, not evidence that a clause applies to a specific procurement",
        ]
        if citations_capped:
            limitations.append(
                f"citations capped at {MAX_CITATIONS}; additional citations not attempted")
        if failures:
            limitations.append(
                f"{failures} citation lookups failed; successful sections remain usable")
        return {
            "sections": rows,
            "record_count": len(rows),
            "version_date": version_date,
            "title_latest_amended_on": title.get("latest_amended_on"),
            "title_up_to_date_as_of": latest,
            "source_url": BROWSE_URL,
            "_provenance": make_provenance_envelope(
                self.name,
                status=status,
                mode="live_exact_section_xml",
                retrieval_mode="live",
                retrieved_at=retrieved_at,
                data_as_of=version_date,
                record_count=len(rows),
                attempts=attempts,
                limitations=limitations,
            ).model_dump(mode="json"),
        }

    def _failure(self, error: str, *, status: str = "failed",
                 retrieved_at: Optional[datetime] = None) -> dict:
        return {
            "sections": [], "record_count": 0, "error": error,
            "source_url": BROWSE_URL,
            "_provenance": make_provenance_envelope(
                self.name,
                status=status,
                mode="live_exact_section_xml",
                retrieval_mode="live",
                retrieved_at=retrieved_at or _utc_now(),
                record_count=0,
                limitations=[error],
            ).model_dump(mode="json"),
        }


try:
    register_source(EcfrTitle48Source)
except ValueError:
    pass
