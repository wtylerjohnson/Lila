"""GSA CALC+ Ceiling Rates adapter — awarded labor-rate intel (data.gov key).

What the government already pays for the client's labor categories across GSA
schedules — pricing ammunition for bids and for the capture brief's dollar
framing. Legacy CALC v2 retired Feb 2025; this targets the v3 CEILINGRATES
API behind CALC+ (docs: https://open.gsa.gov/api/dx-calc-api/), refreshed
nightly. api.gsa.gov authenticates with the api.data.gov key already in .env
(DATA_GOV_API_KEY).
"""

from __future__ import annotations

import os
import statistics
from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

RATES_URL = "https://api.gsa.gov/acquisition/calc/v3/api/ceilingrates/"
PAGE_SIZE = 100
_RATE_FIELDS = (
    "ceiling_rate", "current_price", "price", "rate", "hourly_rate",
)


def _validate_rate_rows(rows: Any) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("CALC response rows were not a list")
    normalized: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("CALC response contained a non-object row")
        if not str(row.get("labor_category") or "").strip():
            raise ValueError("CALC rate row omitted labor_category")
        if not any(field in row for field in _RATE_FIELDS):
            raise ValueError("CALC rate row omitted a recognized rate field")
        normalized.append(row)
    return normalized


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return _validate_rate_rows(payload)
    if isinstance(payload, dict):
        # CALC+ v3 is backed by OpenSearch. The official current response is
        # ``hits.hits[]``, with each rate record nested under ``_source``.
        if "hits" in payload:
            if payload.get("timed_out") is True:
                raise ValueError("CALC OpenSearch request timed out")
            shards = payload.get("_shards")
            if isinstance(shards, dict) and int(shards.get("failed") or 0) > 0:
                raise ValueError("CALC OpenSearch response has failed shards")
            hits = payload["hits"]
            if not isinstance(hits, dict):
                raise ValueError("CALC response field hits was not an object")
            wrapped = hits.get("hits")
            if not isinstance(wrapped, list):
                raise ValueError("CALC response omitted hits.hits list")
            rows = []
            for hit in wrapped:
                if not isinstance(hit, dict):
                    raise ValueError("CALC hits contained a non-object row")
                source = hit.get("_source")
                if not isinstance(source, dict):
                    raise ValueError("CALC hit omitted its _source rate row")
                rows.append(source)
            return _validate_rate_rows(rows)
        for key in ("results", "data", "items", "records"):
            if key not in payload:
                continue
            return _validate_rate_rows(payload[key])
    raise ValueError("CALC response omitted a recognized result list")


def _rate(r: dict) -> float | None:
    for key in ("ceiling_rate", "current_price", "price", "rate", "hourly_rate"):
        try:
            v = float(r.get(key))
            if 5 < v < 2000:  # sanity band for an hourly rate
                return v
        except (TypeError, ValueError):
            continue
    return None


@register_source
class CalcRatesSource(DataSource):
    name = "calc_rates"
    kind = SourceKind.ENRICHMENT

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("DATA_GOV_API_KEY")

    def healthcheck(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "DATA_GOV_API_KEY missing (same key as Regulations.gov)"
        try:
            payload = get_json(
                RATES_URL,
                params={
                    "keyword": "engineer", "page": 1,
                    "page_size": 1, "api_key": self._api_key,
                },
                timeout=8.0,
                retries=1,
            )
            n = len(_rows(payload))
            return True, f"CALC+ v3 ceiling rates reachable ({n} sampled row)"
        except Exception as e:  # noqa: BLE001
            return False, f"key set but call failed: {e}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Rate distribution per keyword-as-labor-category: min/median/max."""
        if not self._api_key:
            raise RuntimeError("DATA_GOV_API_KEY not set (2-min signup at api.data.gov/signup)")
        out: dict[str, Any] = {}
        for kw in (query.keywords or [])[:5]:
            term = kw.strip('"')
            payload = get_json(
                RATES_URL,
                params={
                    "keyword": term,
                    "page": 1,
                    "page_size": PAGE_SIZE,
                    "api_key": self._api_key,
                },
            )
            rates = [x for x in (_rate(r) for r in _rows(payload)) if x is not None]
            if rates:
                out[term] = {
                    "count": len(rates),
                    "min": round(min(rates), 2),
                    "median": round(statistics.median(rates), 2),
                    "max": round(max(rates), 2),
                }
        return out
