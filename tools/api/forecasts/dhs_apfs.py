"""DHS Acquisition Planning Forecast System (APFS) adapter.

Investigated 2026-07-05 (docs/API_SETUP.md): the public forecast list at
apfs-cloud.dhs.gov is backed by an unauthenticated JSON API — no account, no
scraping. Verified live:

    GET https://apfs-cloud.dhs.gov/api/forecast/?format=json&page=N
    -> JSON list (~50 records/page) of forecast dicts (apfs_number,
       requirements_title, requirement, naics "541512 - ...", organization
       "CBP", dollar_range {display_name}, award_quarter "Q4 2026",
       estimated_solicitation_release_date, small_business_set_aside, ...)

Politeness: identified UA, 0.6s between pages, hard page cap, daily cache.
The official unauthenticated API is part of the default forecast boundary;
LILA_ENABLE_DHS_APFS=off remains an explicit incident-response kill switch.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from agents.schemas import ForecastRecord, RawOpportunity
from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

API_URL = os.environ.get("LILA_APFS_URL", "https://apfs-cloud.dhs.gov/api/forecast/")
# VERIFIED LIVE 2026-07-27, 4 record ids pulled from the API above:
#   https://apfs-cloud.dhs.gov/forecast/{id}              -> 404 (all four)
#   https://apfs-cloud.dhs.gov/record/{id}/public-print/  -> 200 (all four)
# The bare list page /forecast/ is still 200 and is unaffected. A delivered
# Red Hat report carried 51 anchors in the dead form; the hand-made golden
# reference used the working one, so this was a producer defect from the
# start, not a site change.
DETAIL_URL = "https://apfs-cloud.dhs.gov/record/{id}/public-print/"
HEADERS = {"User-Agent": "GTM-Group-LILA/1.0 (federal capture research; "
                         "william.tyler.johnson@gmail.com)",
           "Accept": "application/json"}
MAX_PAGES = int(os.environ.get("LILA_APFS_MAX_PAGES", "12"))
PAGE_DELAY_S = 0.6
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "cache" / "forecasts"


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_FORECAST_CACHE_DIR", str(_DEFAULT_CACHE)))


def _naics_split(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """'541512 - Computer Systems Design Services' -> ('541512', label)."""
    if not raw or not isinstance(raw, str):
        return None, None
    parts = raw.split(" - ", 1)
    code = parts[0].strip()
    label = parts[1].strip() if len(parts) > 1 else None
    return (code if code[:6].isdigit() else None), label or raw


def _display(v: Any) -> Optional[str]:
    """APFS nests choice fields as {'display_name': ...}; tolerate plain strings."""
    if isinstance(v, dict):
        return v.get("display_name") or v.get("name")
    return str(v).strip() if v not in (None, "") else None


def _poc(rec: dict) -> Optional[str]:
    first = (rec.get("sbs_coordinator_first_name") or "").strip()
    last = (rec.get("sbs_coordinator_last_name") or "").strip()
    email = (rec.get("sbs_coordinator_email") or "").strip()
    name = " ".join(x for x in (first, last) if x)
    if name and email:
        return f"{name} <{email}>"
    return name or email or None


def map_record(rec: dict, retrieved_at: Optional[datetime] = None) -> ForecastRecord:
    """One APFS dict -> ForecastRecord. Pure; verified against live shape."""
    naics_code, naics_label = _naics_split(rec.get("naics"))
    rid = str(rec.get("id") or rec.get("apfs_number") or "")
    solicitation = (rec.get("estimated_solicitation_release_date")
                    or rec.get("estimated_release_date")
                    or rec.get("award_quarter"))
    return ForecastRecord(
        source="dhs_apfs",
        source_id=str(rec.get("apfs_number") or rid),
        agency="DHS",
        component=_display(rec.get("organization")),
        title=(rec.get("requirements_title") or "").strip(),
        description=(rec.get("requirement") or "").strip() or None,
        naics_code=naics_code,
        naics_label=naics_label,
        estimated_value_range=_display(rec.get("dollar_range")),
        anticipated_solicitation=str(solicitation) if solicitation else None,
        anticipated_award=(str(rec.get("anticipated_award_date"))
                           if rec.get("anticipated_award_date") else
                           _display(rec.get("award_quarter"))),
        fiscal_year=str(rec.get("fiscal_year")) if rec.get("fiscal_year") else None,
        award_type=_display(rec.get("contract_type")) or _display(rec.get("contract_vehicle")),
        set_aside=_display(rec.get("small_business_set_aside")),
        small_business_program=_display(rec.get("small_business_program")),
        small_business_poc=_poc(rec),
        url=DETAIL_URL.format(id=rid or rec.get("apfs_number") or ""),
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
        data_as_of=(
            str(
                rec.get("data_as_of")
                or rec.get("last_modified")
                or rec.get("modified")
                or rec.get("updated_at")
            )
            if (
                rec.get("data_as_of")
                or rec.get("last_modified")
                or rec.get("modified")
                or rec.get("updated_at")
            )
            else None
        ),
        forecast_status=_display(rec.get("current_state")),
    )


def scope_identity(record: ForecastRecord) -> str:
    """Expand source-bound APFS organization codes only for agency screening.

    Keep the record's original fields. Generic agency aliases deliberately do
    not include ambiguous abbreviations such as ICE or DHS (Human Services).
    APFS supplies a known federal parent and slash-delimited component paths.
    """
    from tools.agencies import AGENCIES

    parsed = urlparse(record.url)
    if (record.source != "dhs_apfs"
            or parsed.scheme != "https"
            or parsed.hostname != "apfs-cloud.dhs.gov"
            or record.agency.casefold() not in {
                "dhs", "department of homeland security"}):
        return ""
    parent = next(a for a in AGENCIES if a["abbr"] == "DHS")
    parts = [part.strip().casefold()
             for part in (record.component or "").split("/")]
    component = parts[0] if parts else ""
    if component == "dhs hq":
        component = parts[1] if len(parts) > 1 else ""
    child = next((a for a in AGENCIES
                  if a.get("parent") == "DHS"
                  and component in {a["abbr"].casefold(),
                                    a["name"].casefold()}), None)
    return " ".join([parent["name"], *([child["name"]] if child else [])])


@register_source
class DhsApfsSource(DataSource):
    name = "dhs_apfs"
    kind = SourceKind.DISCOVERY

    def __init__(self) -> None:
        # The unauthenticated official API is a required default forecast
        # origin. Operators may still disable it explicitly during an incident.
        from tools.toggles import is_enabled
        self.enabled = is_enabled(self.name, True)

    def healthcheck(self) -> tuple[bool, str]:
        if not self.enabled:
            return True, "explicitly disabled by LILA_ENABLE_DHS_APFS"
        try:
            page = get_json(API_URL, params={"format": "json", "page": 1},
                            headers=HEADERS, timeout=15.0, retries=1)
            n = len(page) if isinstance(page, list) else 0
            return bool(n), f"public JSON API reachable ({n} records on page 1)"
        except Exception as e:  # noqa: BLE001
            return False, f"unreachable: {e}"

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        """Forecasts are SIGNALS, never live opportunities: this returns []
        by design so no discovery fan-out can mix intent into the live flow.
        Consumers use .forecasts()."""
        return []

    def _fetch_all(self) -> list[dict]:
        """Paged pull with daily cache — one polite crawl per day, ever.

        Live behavior (verified 2026-07-09): the API IGNORES the page param and
        returns the full list every time, so records are deduped by id and the
        crawl stops the first time a page adds nothing new — without this the
        12-page loop returned the same 745 records twelve times."""
        cache = _cache_dir() / f"dhs_apfs_{date.today().isoformat()}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        raw: list[dict] = []
        seen: set = set()
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                time.sleep(PAGE_DELAY_S)
            batch = get_json(API_URL, params={"format": "json", "page": page},
                             headers=HEADERS, timeout=30.0, retries=2)
            if not isinstance(batch, list) or not batch:
                break
            fresh = 0
            for b in batch:
                if not isinstance(b, dict):
                    continue
                key = b.get("id") or b.get("apfs_number") or json.dumps(b, sort_keys=True)[:120]
                if key in seen:
                    continue
                seen.add(key)
                raw.append(b)
                fresh += 1
            if fresh == 0 or len(batch) < 10:  # repeat page or short page = done
                break
        _cache_dir().mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw))
        return raw

    def forecasts(self, query: Optional[SourceQuery] = None) -> list[ForecastRecord]:
        """All current DHS forecast records, mapped. Filtering happens in
        matching.match_forecasts against the capability profile."""
        now = datetime.now(timezone.utc)
        out = []
        for rec in self._fetch_all():
            try:
                m = map_record(rec, retrieved_at=now)
                if m.title:
                    out.append(m)
            except Exception:  # noqa: BLE001 — one malformed record never sinks the pull
                continue
        return out
