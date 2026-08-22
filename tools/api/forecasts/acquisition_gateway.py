"""Government-wide Acquisition Gateway forecast adapter.

The public Forecast Tool is an Angular client, but it reads the official,
anonymous GSA Drupal resource API.  The page's own Export CSV action batches
that same API.  This adapter uses the sanctioned resource and content routes:

    GET https://ag-dashboard.acquisitiongateway.gov/api/v3.0/resources/forecast
    GET https://ag-dashboard.acquisitiongateway.gov/api/v3.0/content/{nid}

Forecasts are planning signals, never live solicitations.  The global listing
is cached once per UTC day; only capability-matched rows need detail calls.
"""

from __future__ import annotations

import fcntl
import html
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.schemas import ForecastRecord
from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

LIST_URL = os.environ.get(
    "LILA_ACQ_GATEWAY_URL",
    "https://ag-dashboard.acquisitiongateway.gov/api/v3.0/resources/forecast",
)
DETAIL_API = os.environ.get(
    "LILA_ACQ_GATEWAY_DETAIL_URL",
    "https://ag-dashboard.acquisitiongateway.gov/api/v3.0/content/{id}",
)
PUBLIC_RECORD_URL = "https://acquisitiongateway.gov/forecast/resources/{id}?nid={id}"
PUBLIC_LIST_URL = "https://acquisitiongateway.gov/forecast"
HEADERS = {
    "User-Agent": (
        "GTM-Group-LILA/1.0 (federal capture research; "
        "william.tyler.johnson@gmail.com)"
    ),
    "Accept": "application/json",
}
PAGE_SIZE = int(os.environ.get("LILA_ACQ_GATEWAY_PAGE_SIZE", "500"))
MAX_PAGES = int(os.environ.get("LILA_ACQ_GATEWAY_MAX_PAGES", "30"))
PAGE_DELAY_S = float(os.environ.get("LILA_ACQ_GATEWAY_PAGE_DELAY_S", "0.35"))
DETAIL_LIMIT = int(os.environ.get("LILA_ACQ_GATEWAY_DETAIL_LIMIT", "75"))
DETAIL_DELAY_S = float(os.environ.get("LILA_ACQ_GATEWAY_DETAIL_DELAY_S", "0.15"))
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "cache" / "forecasts"
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NAICS_RE = re.compile(r"\b(\d{6})\b")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_retrieved(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_FORECAST_CACHE_DIR", str(_DEFAULT_CACHE)))


def _plain(value: Any) -> Optional[str]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        return _plain(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("value", "display_name", "name", "label", "render"):
            if value.get(key) not in (None, ""):
                return _plain(value[key])
        return None
    text = html.unescape(_TAG_RE.sub(" ", str(value)))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _first(rec: dict, *keys: str) -> Optional[str]:
    for key in keys:
        value = _plain(rec.get(key))
        if value:
            return value
    return None


def _date_value(value: Any) -> Optional[str]:
    raw = _plain(value)
    if not raw:
        return None
    match = _DATE_RE.search(raw)
    return match.group(0) if match else raw


def _naics(value: Any) -> tuple[Optional[str], Optional[str]]:
    raw = _plain(value)
    if not raw:
        return None, None
    match = _NAICS_RE.search(raw)
    if not match:
        return None, raw
    code = match.group(1)
    label = raw[match.end():].strip(" :-") or None
    return code, label


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _read_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Acquisition Gateway cache is malformed")
    if not all(isinstance(row, dict) for row in payload["records"]):
        raise ValueError("Acquisition Gateway cache records are malformed")
    return payload


def _snapshot_rank(snapshot: dict[str, Any]) -> tuple[int, int]:
    """Prefer a complete census, then the partial with the most records."""

    return (int(bool(snapshot.get("complete"))), len(snapshot["records"]))


def _promote_daily_snapshot(
    cache: Path,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Atomically promote only a snapshot that improves daily coverage.

    Two clients may refresh concurrently.  Re-read under a cross-process lock
    so a late partial response can never overwrite a complete (or larger)
    snapshot written while this process was on the network.
    """

    cache.parent.mkdir(parents=True, exist_ok=True)
    lock = cache.with_name(f"{cache.name}.lock")
    with lock.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        existing = None
        if cache.exists():
            try:
                existing = _read_snapshot(cache)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        if existing is not None and _snapshot_rank(existing) >= _snapshot_rank(
            candidate
        ):
            return existing, False
        _atomic_json(cache, candidate)
        return candidate, True


def _latest_valid_snapshot(
    *, exclude: set[Path] | None = None
) -> Optional[tuple[Path, dict[str, Any]]]:
    excluded = exclude or set()
    candidates = sorted(_cache_dir().glob("acquisition_gateway_????-??-??.json"))
    for candidate in reversed(candidates):
        if candidate in excluded:
            continue
        try:
            return candidate, _read_snapshot(candidate)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def map_record(rec: dict, retrieved_at: Optional[datetime] = None) -> ForecastRecord:
    """Map either the public listing shape or a tolerant flat fixture shape."""
    rendered = rec.get("render") if isinstance(rec.get("render"), dict) else {}
    values = rec.get("values") if isinstance(rec.get("values"), dict) else {}
    merged = {**values, **rendered, **{
        key: value for key, value in rec.items()
        if key not in {"render", "values"}
    }}
    rid = _first(merged, "field_source_listing_id", "source_id", "listing_id", "uuid")
    nid = _first(merged, "nid", "pid", "id")
    source_id = rid or nid or ""
    naics_code, naics_label = _naics(
        merged.get("field_naics_code")
        or merged.get("naics")
        or merged.get("naics_code")
    )
    agency = _first(
        merged, "field_result_id", "agency", "department", "organization"
    ) or "Federal agency"
    component = _first(
        merged, "field_organization", "component", "sub_agency", "office"
    )
    fiscal_year = _first(merged, "field_estimated_award_fy", "fiscal_year")
    anticipated_award = _first(
        merged,
        "field_estimated_award_fy_qtr",
        "estimated_award_date",
        "award_date",
    ) or fiscal_year
    anticipated_solicitation = _date_value(
        merged.get("field_estimated_solicitation_dat")
        or merged.get("estimated_solicitation_date")
        or merged.get("solicitation_date")
        or merged.get("target_solicitation")
    )
    return ForecastRecord(
        source="acquisition_gateway",
        source_id=source_id,
        agency=agency,
        component=component,
        title=_first(merged, "title", "requirement_title") or "",
        description=_first(merged, "body", "description", "requirement_description"),
        naics_code=naics_code,
        naics_label=naics_label,
        psc=_first(merged, "psc", "product_service_code"),
        estimated_value_range=_first(
            merged,
            "field_estimated_contract_v_max",
            "estimated_value",
            "dollar_value_range",
        ),
        anticipated_solicitation=anticipated_solicitation,
        anticipated_award=anticipated_award,
        fiscal_year=fiscal_year,
        award_type=_first(merged, "field_contract_type", "contract_vehicle", "contract_type"),
        set_aside=_first(merged, "field_acquisition_strategy", "set_aside", "small_business_set_aside"),
        incumbent_stated=_first(merged, "field_contractor_name", "incumbent", "incumbent_contractor"),
        url=PUBLIC_RECORD_URL.format(id=nid) if nid else PUBLIC_LIST_URL,
        retrieved_at=retrieved_at or _now(),
        data_as_of=_date_value(
            merged.get("changed")
            or merged.get("last_modified")
            or merged.get("field_last_modified")
        ),
        forecast_status=_first(merged, "field_award_status", "status"),
    )


def _detail_scalar(payload: dict, key: str) -> Optional[str]:
    value = payload.get(key)
    return _plain(value)


def _detail_date(payload: dict, key: str) -> Optional[str]:
    value = payload.get(key)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return _date_value(value[0].get("value"))
    return _date_value(value)


@register_source
class AcquisitionGatewaySource(DataSource):
    name = "acquisition_gateway"
    kind = SourceKind.DISCOVERY
    forecast_agency = "Government-wide Acquisition Gateway"

    def __init__(self) -> None:
        # The public production resource endpoint was verified 2026-07-20.
        # Operators can still disable it explicitly during incident response.
        raw = os.environ.get("LILA_ENABLE_ACQ_GATEWAY", "on").strip().lower()
        self.enabled = raw not in {"0", "false", "off", "no"}
        self.last_provenance: dict[str, Any] = {}

    def healthcheck(self) -> tuple[bool, str]:
        if not self.enabled:
            return True, "explicitly disabled by LILA_ENABLE_ACQ_GATEWAY"
        try:
            payload = get_json(
                LIST_URL,
                params={"range": 1},
                headers=HEADERS,
                timeout=20.0,
                retries=1,
            )
            listing = payload.get("listing") if isinstance(payload, dict) else None
            count = len((listing or {}).get("data") or [])
            total = (listing or {}).get("total")
            return bool(count), f"public forecast API reachable (1 of {total} rows)"
        except Exception as exc:  # noqa: BLE001
            return False, f"public forecast API unavailable: {exc}"

    def search(self, query: SourceQuery) -> list:
        return []  # forecasts NEVER enter the live-opportunity flow

    def _live_snapshot(self) -> dict[str, Any]:
        records: list[dict] = []
        seen: set[str] = set()
        total_available: Optional[int] = None
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                time.sleep(PAGE_DELAY_S)
            # The public Forecast UI's export client omits ``page`` for the
            # first batch, then sends one-based page numbers beginning at 2.
            # The API aliases page=1 to page=2, so sending 1 then 2 returns the
            # same batch twice and makes a complete census look capped.
            params = {"range": PAGE_SIZE}
            if page > 1:
                params["page"] = page
            payload: Any = get_json(
                LIST_URL,
                params=params,
                headers=HEADERS,
                timeout=60.0,
                retries=2,
            )
            listing = payload.get("listing") if isinstance(payload, dict) else None
            if not isinstance(listing, dict):
                raise ValueError("Acquisition Gateway response has no listing")
            if total_available is None:
                try:
                    total_available = int(listing.get("total"))
                except (TypeError, ValueError):
                    total_available = None
            batch = listing.get("data") or []
            if not isinstance(batch, list):
                raise ValueError("Acquisition Gateway listing data is not a list")
            fresh = 0
            for row in batch:
                if not isinstance(row, dict):
                    continue
                key = str(row.get("nid") or row.get("pid") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                records.append(row)
                fresh += 1
            if not batch or not fresh:
                break
            if total_available is not None and len(records) >= total_available:
                break
        complete = total_available is not None and len(records) >= total_available
        return {
            "retrieved_at": _now().isoformat(timespec="seconds"),
            "total_available": total_available,
            "complete": complete,
            "records": records,
        }

    def _fetch_all(self) -> list[dict]:
        cache = _cache_dir() / f"acquisition_gateway_{_today().isoformat()}.json"
        current_partial: Optional[dict[str, Any]] = None
        if cache.exists():
            try:
                snapshot = _read_snapshot(cache)
                if bool(snapshot.get("complete")):
                    self.last_provenance = {
                        "mode": "official_daily_cache",
                        "retrieved_at": snapshot.get("retrieved_at"),
                        "total_available": snapshot.get("total_available"),
                        "complete": True,
                        "records": len(snapshot["records"]),
                        "source_url": PUBLIC_LIST_URL,
                    }
                    return snapshot["records"]
                # A current-day partial snapshot is usable evidence, but it is
                # not a reason to suppress a repair attempt. Keep it available
                # as the truthful fallback if the live retry also fails.
                current_partial = snapshot
            except (OSError, ValueError, json.JSONDecodeError):
                # Corrupt current cache is replaced by the official live pull.
                pass
        try:
            live_snapshot = self._live_snapshot()
            snapshot, promoted = _promote_daily_snapshot(cache, live_snapshot)
            complete = bool(snapshot.get("complete"))
            if promoted:
                mode = "live_official_api"
                limitation = None
            elif complete:
                mode = "official_daily_cache_after_concurrent_refresh"
                limitation = None
            else:
                mode = "partial_current_day_cache"
                limitation = (
                    "current-day official snapshot remains incomplete; the "
                    "live repair did not improve cached coverage"
                )
            self.last_provenance = {
                "mode": mode,
                "retrieved_at": snapshot["retrieved_at"],
                "total_available": snapshot.get("total_available"),
                "complete": complete,
                "records": len(snapshot["records"]),
                "source_url": PUBLIC_LIST_URL,
            }
            if limitation:
                self.last_provenance["limitation"] = limitation
            return snapshot["records"]
        except Exception as live_error:  # noqa: BLE001
            if current_partial is not None:
                snapshot, _ = _promote_daily_snapshot(cache, current_partial)
                complete = bool(snapshot.get("complete"))
                self.last_provenance = {
                    "mode": (
                        "official_daily_cache_after_concurrent_refresh"
                        if complete else "partial_current_day_cache"
                    ),
                    "retrieved_at": snapshot.get("retrieved_at"),
                    "total_available": snapshot.get("total_available"),
                    "complete": complete,
                    "records": len(snapshot["records"]),
                    "source_url": PUBLIC_LIST_URL,
                    "private_error": str(live_error),
                }
                if not complete:
                    self.last_provenance["limitation"] = (
                        "current-day official snapshot is incomplete; live "
                        "refresh failed, so the partial snapshot was retained"
                    )
                return snapshot["records"]
            latest = _latest_valid_snapshot(exclude={cache})
            if latest is None:
                raise
            _path, snapshot = latest
            self.last_provenance = {
                "mode": "stale_official_cache",
                "retrieved_at": snapshot.get("retrieved_at"),
                "total_available": snapshot.get("total_available"),
                "complete": bool(snapshot.get("complete")),
                "records": len(snapshot["records"]),
                "source_url": PUBLIC_LIST_URL,
                "limitation": "live public API failed; using newest official snapshot",
                "private_error": str(live_error),
            }
            return snapshot["records"]

    def forecasts(self) -> list[ForecastRecord]:
        if not self.enabled:
            self.last_provenance = {
                "mode": "not_run",
                "complete": False,
                "records": 0,
                "source_url": PUBLIC_LIST_URL,
            }
            return []
        rows = self._fetch_all()
        observed_at = (
            _parse_retrieved(self.last_provenance.get("retrieved_at")) or _now()
        )
        out: list[ForecastRecord] = []
        for row in rows:
            try:
                mapped = map_record(row, retrieved_at=observed_at)
                if mapped.source_id and mapped.title:
                    out.append(mapped)
            except Exception:  # noqa: BLE001 — one malformed row cannot sink the census
                continue
        return out

    def enrich_matched(
        self, records: list[ForecastRecord],
    ) -> list[ForecastRecord]:
        """Fetch official details only for preliminary capability matches."""
        if not records:
            return []
        cache_path = (
            _cache_dir()
            / f"acquisition_gateway_details_{_today().isoformat()}.json"
        )
        details: dict[str, dict] = {}
        if cache_path.exists():
            try:
                loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    details = {
                        str(key): value for key, value in loaded.items()
                        if isinstance(value, dict)
                    }
            except (OSError, json.JSONDecodeError):
                details = {}
        changed = False
        enriched: list[ForecastRecord] = []
        for index, record in enumerate(records[:DETAIL_LIMIT]):
            match = re.search(r"/resources/(\d+)", record.url)
            nid = match.group(1) if match else ""
            if not nid:
                enriched.append(record)
                continue
            detail = details.get(nid)
            if detail is None:
                if index:
                    time.sleep(DETAIL_DELAY_S)
                try:
                    payload = get_json(
                        DETAIL_API.format(id=nid),
                        headers=HEADERS,
                        timeout=30.0,
                        retries=2,
                    )
                except Exception:  # noqa: BLE001 — the listing remains usable
                    enriched.append(record)
                    continue
                if not isinstance(payload, dict):
                    enriched.append(record)
                    continue
                detail = payload
                details[nid] = payload
                changed = True
            solicitation = _detail_date(
                detail, "field_estimated_solicitation_dat"
            )
            incumbent = _detail_scalar(detail, "field_contractor_name")
            advisor_name = _detail_scalar(detail, "field_advisor_info_name")
            advisor_email = _detail_scalar(detail, "field_advisor_info_email")
            advisor = " ".join(
                part for part in (
                    advisor_name,
                    f"<{advisor_email}>" if advisor_email else None,
                )
                if part
            ) or None
            enriched.append(record.model_copy(update={
                "anticipated_solicitation": (
                    solicitation or record.anticipated_solicitation
                ),
                "incumbent_stated": incumbent or record.incumbent_stated,
                "small_business_poc": advisor or record.small_business_poc,
            }))
        enriched.extend(records[DETAIL_LIMIT:])
        # THE DETAIL BUDGET IS DISCLOSED (audit 2026-07-30): records past the
        # cap pass through without incumbent name or refined solicitation
        # date, and incumbent identity is the displacement-targeting field.
        # A pass-through must never read as an enriched record that happens
        # to lack an incumbent.
        self.last_provenance.update({
            "detail_candidates": len(records),
            "detail_enriched": min(len(records), DETAIL_LIMIT),
            "detail_truncated": len(records) > DETAIL_LIMIT,
        })
        if changed:
            _atomic_json(cache_path, details)
        return enriched
