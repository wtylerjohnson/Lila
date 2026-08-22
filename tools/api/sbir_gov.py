"""SBIR.gov adapter — open innovation-pipeline solicitations (keyless).

SBIR/STTR topics show which agencies are funding the client's technology
category at the R&D stage — demand forming 1-3 years before production buys,
and direct opportunities when the client (or its partners) qualifies as a
small business.

API (grounded from https://www.sbir.gov/api/solicitation — verified host
2026-07-03; the bare api.sbir.gov hostname does not resolve):
    GET https://api.www.sbir.gov/public/api/solicitations?open=1&rows=50
    Response: JSON list of {solicitation_title, solicitation_number, agency,
              close_date, open_date, sbir_solicitation_link, solicitation_topics}

The official API is currently under maintenance and has returned HTTP 429.
One all-open pull is therefore shared across every client keyword lane and
cached by retrieval date.  A failed live pull may use the newest older official
snapshot, but every row and the provenance block say that it is stale and name
the snapshot date; a stale snapshot is never represented as a fresh API result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from tools.api._http import get_json, get_text
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.text_match import matching_phrases, phrase_matches

SOLICITATIONS_URL = "https://api.www.sbir.gov/public/api/solicitations"
TOPICS_URL = "https://www.sbir.gov/topics"
TOPIC_HEADERS = {
    "User-Agent": "Federal-Sales-OS/1.0 (official public-data research)",
    "Accept": "text/html",
}
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "sbir"
PAGE_SIZE = 50  # documented maximum
MAX_PAGES = 10
MAX_TOPIC_PAGES = 20


def _utc_now() -> datetime:
    """One injectable clock owns snapshot naming, lookup, and age math."""
    return datetime.now(timezone.utc)


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_SBIR_CACHE_DIR", str(_DEFAULT_CACHE)))


def _items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        key = next(
            (candidate for candidate in ("results", "data")
             if candidate in payload),
            None,
        )
        if key is None:
            raise ValueError(
                "SBIR solicitation response requires results or data"
            )
        rows = payload[key]
        if not isinstance(rows, list):
            raise ValueError("SBIR solicitation rows must be a list")
    else:
        raise ValueError("SBIR solicitation response has an invalid shape")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("SBIR solicitation rows must contain objects")
    for row in rows:
        number = str(row.get("solicitation_number") or "").strip()
        title = str(
            row.get("solicitation_title") or row.get("title") or ""
        ).strip()
        if not number or not title:
            raise ValueError(
                "SBIR solicitation row omitted number or title"
            )
    return rows


def _read_cache(
    path: Path,
) -> tuple[list[dict], str, str, bool, str | None]:
    payload = json.loads(path.read_text())
    rows = payload.get("records") or []
    retrieved_at = payload.get("retrieved_at") or f"{path.stem.removeprefix('open_')}T00:00:00+00:00"
    origin = payload.get("origin") or "solicitation_api"
    if not isinstance(rows, list):
        raise ValueError("SBIR cache records must be a list")
    rows = _items(rows)
    cached_partial = payload.get("partial")
    if isinstance(cached_partial, bool):
        partial = cached_partial
        limitations = payload.get("limitations")
    else:
        # Old cache snapshots were produced under hard page caps without a
        # completeness bit. They remain usable, but cannot claim full census.
        partial = True
        limitations = "snapshot predates SBIR completeness metadata"
    return (rows, str(retrieved_at), str(origin), partial,
            str(limitations) if limitations else None)


def _latest_cache(
    *, exclude: set[Path] | None = None, not_after: date | None = None,
) -> Path | None:
    root = _cache_dir()
    if not root.exists():
        return None
    excluded = exclude or set()
    for candidate in reversed(sorted(root.glob("open_*.json"))):
        if candidate in excluded:
            continue
        try:
            _rows, retrieved_at, _origin, _partial, _limitations = (
                _read_cache(candidate)
            )
            retrieved_on = date.fromisoformat(retrieved_at[:10])
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not_after is not None and retrieved_on > not_after:
            continue
        return candidate
    return None


def _write_cache(
    records: list[dict],
    retrieved_at: str,
    *,
    origin: str,
    partial: bool,
    limitations: str | None = None,
) -> None:
    root = _cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"open_{retrieved_at[:10]}.json"
    payload = {
        "retrieved_at": retrieved_at,
        "source": TOPICS_URL if origin == "topics_listing" else SOLICITATIONS_URL,
        "origin": origin,
        "partial": partial,
        "query": {"open": 1, "rows": PAGE_SIZE},
        "records": records,
    }
    if limitations:
        payload["limitations"] = limitations
    fd, tmp_name = tempfile.mkstemp(
        dir=root, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = " ".join(raw.split()).strip()
    try:
        return datetime.strptime(cleaned, "%B %d, %Y").date().isoformat()
    except ValueError:
        return cleaned or None


class _TopicListingParser(HTMLParser):
    """Parse semantic result cards from SBA's server-rendered public listing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict] = []
        self.current: dict | None = None
        self.capture: str | None = None
        self.buffer: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def _finish_capture(self) -> None:
        if self.current is None or self.capture is None:
            return
        text = " ".join("".join(self.buffer).split())
        if self.capture == "title":
            self.current["solicitation_title"] = text
        elif self.capture == "description":
            topics = self.current.setdefault("solicitation_topics", [{}])
            topics[0]["topic_description"] = text
        elif self.capture == "dates":
            release = re.search(r"Release Date:\s*(.*?)\s+Open Date:", text)
            opened = re.search(r"Open Date:\s*(.*?)\s+Close Date:", text)
            closed = re.search(r"Close Date:\s*(.*)$", text)
            self.current["release_date"] = _parse_date(
                release.group(1) if release else None)
            self.current["open_date"] = _parse_date(
                opened.group(1) if opened else None)
            self.current["close_date"] = _parse_date(
                closed.group(1) if closed else None)
        self.capture = None
        self.buffer = []

    def _finish_record(self) -> None:
        self._finish_capture()
        if self.current and self.current.get("solicitation_title"):
            topic = (self.current.get("solicitation_topics") or [{}])[0]
            topic.setdefault("topic_title", self.current["solicitation_title"])
            topic.setdefault("sbir_topic_link", self.current.get("solicitation_agency_url"))
            self.records.append(self.current)
        self.current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = self._attrs(attrs)
        if tag == "a" and values.get("href", "").startswith("/topics/"):
            self._finish_record()
            url = urljoin(TOPICS_URL, values["href"])
            topic_id = values["href"].rstrip("/").rsplit("/", 1)[-1]
            self.current = {
                "solicitation_number": topic_id,
                "solicitation_agency_url": url,
                "current_status": "Open",
                "solicitation_topics": [{"sbir_topic_link": url}],
            }
            self.capture = "title"
            self.buffer = []
        elif self.current is not None and tag == "p":
            classes = set(values.get("class", "").split())
            if "margin-bottom-205" in classes:
                self._finish_capture()
                self.capture = "dates"
                self.buffer = []
            elif "measure-6" in classes:
                self._finish_capture()
                self.capture = "description"
                self.buffer = []
        elif self.current is not None and tag == "img":
            prefix = "Seal of the Agency:"
            alt = values.get("alt", "")
            if alt.startswith(prefix):
                self.current["agency"] = alt.removeprefix(prefix).strip()

    def handle_endtag(self, tag: str) -> None:
        if ((tag == "a" and self.capture == "title")
                or (tag == "p" and self.capture in {"dates", "description"})):
            self._finish_capture()

    def handle_data(self, data: str) -> None:
        if self.capture is not None:
            self.buffer.append(data)

    def close(self) -> None:
        super().close()
        self._finish_record()


def _fetch_topics_listing_snapshot() -> tuple[list[dict], bool]:
    """Return official listing rows and whether the configured cap was hit."""
    records: list[dict] = []
    seen: set[str] = set()
    complete = False
    for page in range(MAX_TOPIC_PAGES):
        url = f"{TOPICS_URL}?{urlencode({'status': 'Open', 'page': page})}"
        parser = _TopicListingParser()
        parser.feed(get_text(url, headers=TOPIC_HEADERS, retries=2))
        parser.close()
        fresh = []
        for row in parser.records:
            key = str(row.get("solicitation_agency_url") or "")
            if key and key not in seen:
                seen.add(key)
                fresh.append(row)
        records.extend(fresh)
        if len(parser.records) < 10 or not fresh:
            complete = True
            break
    if not records:
        raise RuntimeError("SBIR.gov Topics listing returned no parseable open records")
    return records, not complete


def _fetch_topics_listing() -> list[dict]:
    """Compatibility wrapper for the official current public listing."""
    records, _partial = _fetch_topics_listing_snapshot()
    return records


def _fetch_open() -> tuple[list[dict], dict[str, Any]]:
    """Return the official all-open snapshot plus inspectable provenance."""
    run_now = _utc_now()
    snapshot_day = run_now.date()
    retrieved_at = run_now.isoformat(timespec="seconds")
    today_cache = _cache_dir() / f"open_{snapshot_day.isoformat()}.json"
    cache_attempts: list[dict] = []
    invalid_caches: set[Path] = set()
    if today_cache.exists():
        try:
            (records, cache_retrieved_at, origin, partial,
             limitations) = _read_cache(today_cache)
            provenance = {
                "source": "SBIR.gov Solicitation API",
                "endpoint": (TOPICS_URL if origin == "topics_listing"
                             else SOLICITATIONS_URL),
                "mode": "official_daily_cache",
                "cache_origin": origin,
                "freshness": "current_day_snapshot",
                "stale": False,
                "status": "partial" if partial else "success",
                "partial": partial,
                "retrieved_at": cache_retrieved_at,
                "source_attempts": [
                    {"source": "daily_cache", "status": (
                        "partial" if partial else "success"
                    )}],
            }
            if limitations:
                provenance["limitations"] = limitations
            if not partial:
                return records, provenance
            # Partial and legacy current-day snapshots remain valid fallback
            # evidence, but they should not freeze the lane in a partial state
            # for the rest of the UTC day. Try the sanctioned live chain once;
            # the dated-cache branch below will recover this snapshot if both
            # official live surfaces are still unavailable.
            cache_attempts.extend(provenance["source_attempts"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            invalid_caches.add(today_cache)
            cache_attempts.append({
                "source": "daily_cache", "status": "failed",
                "error": f"invalid current snapshot: {exc}",
            })

    try:
        records: list[dict] = []
        complete = False
        for page in range(MAX_PAGES):
            batch = _items(get_json(
                SOLICITATIONS_URL,
                params={"open": 1, "rows": PAGE_SIZE, "start": page * PAGE_SIZE},
                retries=1,
            ))
            records.extend(batch)
            if len(batch) < PAGE_SIZE:
                complete = True
                break
        partial = not complete
        limitations = (
            f"Solicitation API paging capped at {MAX_PAGES * PAGE_SIZE} rows"
            if partial else None
        )
        _write_cache(
            records,
            retrieved_at,
            origin="solicitation_api",
            partial=partial,
            limitations=limitations,
        )
        provenance = {
            "source": "SBIR.gov Solicitation API",
            "endpoint": SOLICITATIONS_URL,
            "mode": "live_official_api",
            "freshness": "live",
            "stale": False,
            "status": "partial" if partial else "success",
            "partial": partial,
            "retrieved_at": retrieved_at,
            "source_attempts": cache_attempts + [
                {"source": "solicitation_api", "status": (
                    "partial" if partial else "success"
                )}],
        }
        if limitations:
            provenance["limitations"] = limitations
        return records, provenance
    except Exception as exc:  # noqa: BLE001 — official listing is sanctioned fallback
        api_error = str(exc)

    try:
        records, partial = _fetch_topics_listing_snapshot()
        limitations = (
            "SBIR.gov states that topic copies may trail the participating "
            "agency; verify the linked agency solicitation before action"
        )
        if partial:
            limitations += (
                f"; official Topics listing paging capped at "
                f"{MAX_TOPIC_PAGES} pages"
            )
        _write_cache(
            records,
            retrieved_at,
            origin="topics_listing",
            partial=partial,
            limitations=limitations,
        )
        return records, {
            "source": "SBIR.gov Funding Opportunities listing",
            "endpoint": TOPICS_URL,
            "mode": "live_official_topics_listing",
            "freshness": "current_official_listing",
            "stale": False,
            "status": "partial" if partial else "success",
            "partial": partial,
            "retrieved_at": retrieved_at,
            "limitations": limitations,
            "source_attempts": cache_attempts + [
                {"source": "solicitation_api", "status": "failed",
                 "error": api_error},
                {"source": "official_topics_listing", "status": (
                    "partial" if partial else "success"
                )},
            ],
        }
    except Exception as listing_exc:  # noqa: BLE001 — dated cache is final fallback
        cached = _latest_cache(
            exclude=invalid_caches, not_after=snapshot_day)
        if cached is None:
            raise RuntimeError(
                "SBIR Solicitation API and official Topics listing failed: "
                f"API={api_error}; listing={listing_exc}"
            ) from listing_exc
        (records, retrieved_at, origin, partial,
         limitations) = _read_cache(cached)
        retrieved_on = date.fromisoformat(retrieved_at[:10])
        current_day_fallback = cached == today_cache
        provenance = {
            "source": "SBIR.gov official snapshot",
            "endpoint": TOPICS_URL if origin == "topics_listing" else SOLICITATIONS_URL,
            "mode": (
                "partial_current_day_cache"
                if current_day_fallback else "stale_official_cache"
            ),
            "cache_origin": origin,
            "freshness": (
                "current_day_snapshot" if current_day_fallback else "stale"
            ),
            "stale": not current_day_fallback,
            "status": "partial" if partial else "success",
            "partial": partial,
            "retrieved_at": retrieved_at,
            "age_days": (snapshot_day - retrieved_on).days,
            "live_error": f"API={api_error}; listing={listing_exc}",
            "source_attempts": cache_attempts + [
                {"source": "solicitation_api", "status": "failed", "error": api_error},
                {"source": "official_topics_listing", "status": "failed",
                 "error": str(listing_exc)},
                {"source": (
                    "current_day_official_cache"
                    if current_day_fallback else "dated_official_cache"
                ), "status": (
                    "partial" if partial else "success"
                )},
            ],
        }
        if limitations:
            provenance["limitations"] = limitations
        return records, provenance


def _search_text(row: dict) -> str:
    parts = [row.get("solicitation_title"), row.get("title")]
    for topic in row.get("solicitation_topics") or []:
        if not isinstance(topic, dict):
            continue
        parts.extend((topic.get("topic_title"), topic.get("topic_description")))
        for subtopic in topic.get("subtopics") or []:
            if isinstance(subtopic, dict):
                parts.extend((subtopic.get("subtopic_title"),
                              subtopic.get("subtopic_description")))
    return " ".join(str(part) for part in parts if part).lower()


def _official_url(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        try:
            parsed = urlparse(text)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() == "https"
            and (host.endswith(".gov") or host.endswith(".mil"))
        ):
            return text
    return ""


def _program_records(
    row: dict,
    *,
    matched_terms: list[str],
    retrieved_at: str,
) -> list[dict[str, Any]]:
    number = str(row.get("solicitation_number") or "").strip()
    title = str(
        row.get("solicitation_title") or row.get("title") or ""
    ).strip()
    agency = str(row.get("agency") or "").strip()
    topics = [
        value for value in (row.get("solicitation_topics") or [])
        if isinstance(value, dict)
    ]
    if not number or not title or not agency:
        return []
    candidates = topics or [{}]
    normalized_rows = []
    for index, topic in enumerate(candidates):
        topic_title = str(topic.get("topic_title") or title).strip()
        canonical_url = _official_url(
            topic.get("sbir_topic_link"),
            row.get("sbir_solicitation_link"),
            row.get("solicitation_agency_url"),
        )
        if not topic_title or not canonical_url:
            continue
        topic_code = str(
            topic.get("topic_code") or topic.get("topic_number")
            or topic.get("topic_id") or ""
        ).strip()
        if len(candidates) == 1:
            record_id = f"sbir_gov:{number}"
        else:
            if not topic_code:
                digest = hashlib.sha256(
                    f"{number}\n{topic_title}\n{canonical_url}".encode()
                ).hexdigest()[:16]
                topic_code = digest
            record_id = f"sbir_gov:{number}:{topic_code}"
        normalized: dict[str, Any] = {
            "record_id": record_id,
            "canonical_url": canonical_url,
            "source": "sbir_gov",
            "tier": "program",
            "kind": "small_business_innovation_topic",
            "agency": agency,
            "title": topic_title,
            "number": number,
            "topic_code": topic_code or None,
            "topic_index": index,
            "program": row.get("program"),
            "status": row.get("current_status") or "Open",
            "release_date": row.get("release_date"),
            "open_date": row.get("open_date"),
            "close_date": row.get("close_date"),
            "matched_terms": list(matched_terms),
            "promotion_eligible": False,
            "live_solicitation": False,
            "retrieved_at": retrieved_at,
            "data_as_of": retrieved_at[:10],
        }
        objective = str(topic.get("topic_description") or "").strip()
        if objective:
            normalized["objective"] = objective
        normalized_rows.append(normalized)
    return normalized_rows


@register_source
class SbirSource(DataSource):
    name = "sbir_gov"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            get_json(SOLICITATIONS_URL, params={"keyword": "cyber", "rows": 1},
                     timeout=8.0, retries=1)
            return True, "api.sbir.gov reachable (keyless)"
        except Exception as e:  # noqa: BLE001
            api_error = str(e)
        try:
            url = f"{TOPICS_URL}?{urlencode({'status': 'Open', 'page': 0})}"
            parser = _TopicListingParser()
            parser.feed(get_text(
                url, headers=TOPIC_HEADERS, timeout=8.0, retries=1
            ))
            parser.close()
            if parser.records:
                return True, (
                    "SBIR API unavailable; official Topics listing reachable "
                    f"({len(parser.records)} records on first page)"
                )
            return False, (
                "SBIR API unavailable and official Topics listing returned no "
                f"parseable records: {api_error}"
            )
        except Exception as listing_exc:  # noqa: BLE001
            return False, (
                "SBIR API and official Topics listing unreachable: "
                f"API={api_error}; listing={listing_exc}"
            )

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Open SBIR/STTR solicitations, fetched once and matched locally."""
        records, provenance = _fetch_open()
        out: dict[str, Any] = {"_provenance": {
            **provenance,
            "records_received": len(records),
        }}
        # The all-open pull is shared across lanes, so screening every approved
        # term costs no additional source requests. A former first-five slice
        # silently missed records that matched only later capability terms.
        keywords: list[str] = []
        seen_keywords: set[str] = set()
        for raw in query.keywords or []:
            term = str(raw or "").strip()
            normalized = term.casefold()
            if not term or normalized in seen_keywords:
                continue
            seen_keywords.add(normalized)
            keywords.append(term)
        program_by_id: dict[str, dict[str, Any]] = {}
        for source_row in records:
            text = _search_text(source_row)
            matched_terms = matching_phrases(text, keywords)
            for normalized in _program_records(
                    source_row,
                    matched_terms=matched_terms,
                    retrieved_at=str(provenance["retrieved_at"])):
                program_by_id.setdefault(normalized["record_id"], normalized)
        program_rows = list(program_by_id.values())
        program_rows.sort(key=lambda row: (
            str(row.get("close_date") or "9999-12-31"),
            str(row.get("title") or ""),
            str(row.get("record_id") or ""),
        ))
        out["records"] = program_rows
        out["_provenance"]["records_matched"] = sum(
            bool(row.get("matched_terms")) for row in program_rows
        )
        out["_provenance"]["tier"] = "program"
        out["_provenance"]["promotion_eligible"] = False
        out["_provenance"]["consumer"] = "prospective_horizon"
        out["_provenance"]["query_terms_screened"] = len(keywords)
        for kw in keywords:
            items = [
                row for row in records
                if phrase_matches(_search_text(row), kw)
            ]
            rows = []
            for s in items[:5]:
                rows.append({
                    "title": s.get("solicitation_title") or s.get("title"),
                    "number": s.get("solicitation_number"),
                    "agency": s.get("agency"),
                    "program": s.get("program"),
                    "close": s.get("close_date"),
                    "url": s.get("sbir_solicitation_link") or s.get("solicitation_agency_url"),
                    "topic_count": len(s.get("solicitation_topics") or [])
                    if isinstance(s.get("solicitation_topics"), list) else None,
                    "matched": kw,
                    "freshness": provenance["freshness"],
                    "status_as_of": provenance["retrieved_at"],
                })
            if rows:
                out[kw] = rows
        return out
