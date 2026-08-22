"""GSA IT Collect public API adapter for investment/contract context.

The API's contract rows link PIIDs to an IT investment (serviceId).  They are
portfolio/CPIC context, not obligations, spend, or opportunity records.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from tools.api._connector_wave3 import failure, result, stable_id, text
from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "gsa_it_collect"
API_ROOT = "https://api.gsa.gov/technology/it-collect"
DOCS_URL = ("https://gsa.github.io/ITDB-schema/public-by-2026/api/data/gov/"
            "docs/itcollect_openapi.json")
SERVICE_RE = re.compile(r"\bINV\d+\b", re.I)
MAX_SERVICE_LANES = 8
MAX_RETURNED = 250


def _collection(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("hydra:member", "member", "items", "data"):
            if isinstance(value.get(key), list):
                return [row for row in value[key] if isinstance(row, dict)]
    raise ValueError("IT Collect response has no collection")


def _normalize(service_id: str, native: dict[str, Any]) -> Optional[dict[str, Any]]:
    piid = text(native.get("PIID") or native.get("contractPIID"))
    agency_id = text(native.get("agencyId"))
    if not piid and not agency_id:
        return None
    service = text(native.get("serviceId")) or service_id
    return {
        "record_id": stable_id(SOURCE_NAME, service, agency_id, piid),
        "service_id": service,
        "agency_id": agency_id,
        "agency_code": text(native.get("agencyCode")),
        "piid": piid,
        "reference_piid": text(native.get("referencePIID")),
        "created": text(native.get("created")),
        "last_modified": text(native.get("lastModified")),
        "_join_keys": {"investment_id": service, "piid": piid,
                       "reference_piid": text(native.get("referencePIID")),
                       "agency_code": text(native.get("agencyCode"))},
        "evidence_semantics": "it_portfolio_context_not_opportunity_or_obligation",
    }


class GsaItCollectSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetch_json: Optional[Callable[..., Any]] = None,
                 api_key: Optional[str] = None) -> None:
        self._fetch_json = fetch_json or get_json
        self._api_key = api_key

    def _key(self) -> Optional[str]:
        return self._api_key or os.environ.get("GSA_API_KEY") or os.environ.get("API_DATA_GOV_KEY")

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("portfolio context is enrichment-only; use portfolio_context()")

    def healthcheck(self) -> tuple[bool, str]:
        key = self._key()
        if not key:
            return False, "GSA_API_KEY or API_DATA_GOV_KEY is required (free api.data.gov key)"
        try:
            payload = self._fetch_json(
                API_ROOT + "/v1/bureaus", params={"api_key": key},
                timeout=10.0, retries=1)
            return True, f"official IT Collect API reachable ({len(_collection(payload))} bureaus)"
        except Exception as exc:  # noqa: BLE001
            return False, f"IT Collect unavailable: {exc}"

    def portfolio_context(self, query: SourceQuery,
                          service_ids: Optional[list[str]] = None) -> dict[str, Any]:
        lanes = [str(value).upper() for value in (service_ids or []) if SERVICE_RE.fullmatch(str(value))]
        for value in query.keywords:
            lanes.extend(found.upper() for found in SERVICE_RE.findall(str(value)))
        lanes = list(dict.fromkeys(lanes))
        if not lanes:
            return result(
                self.name, items=[], status="partial", mode="refused_unscoped",
                url=DOCS_URL,
                limitations=["unscoped feed pull refused; pass one or more INV#### service/investment IDs"],
            )
        lanes_capped = len(lanes) > MAX_SERVICE_LANES
        lanes = lanes[:MAX_SERVICE_LANES]
        key = self._key()
        if not key:
            return failure(
                self.name, mode="live_official_api_server_scoped", url=API_ROOT,
                error="missing GSA_API_KEY or API_DATA_GOV_KEY",
                limitations=["DEMO_KEY is intentionally not used because its shared daily quota is unreliable"],
            )
        items: list[dict[str, Any]] = []
        native_receipts: dict[str, Any] = {}
        attempts: list[dict[str, Any]] = []
        for service_id in lanes:
            url = API_ROOT + f"/v1/services/{service_id}/contracts"
            try:
                native = _collection(self._fetch_json(
                    url, params={"api_key": key}, timeout=20.0, retries=2))
                native_receipts[service_id] = native
                normalized = [row for row in (_normalize(service_id, item) for item in native) if row]
                items.extend(normalized)
                attempts.append({"source": service_id, "status": "success",
                                 "count": len(normalized)})
            except Exception as exc:  # noqa: BLE001 - lane isolation
                attempts.append({"source": service_id, "status": "failed", "count": 0,
                                 "error": f"{type(exc).__name__}: {exc}"})
        failed = sum(row["status"] == "failed" for row in attempts)
        cap = max(1, min(query.limit, MAX_RETURNED))
        kept = items[:cap]
        status = ("failed" if failed == len(attempts) else "partial"
                  if failed or lanes_capped or len(items) > cap else "complete")
        limitations = [
            "IT Collect contract rows are CPIC/portfolio linkage, not obligations, spend, or opportunities",
            "agencies are no longer required to report via IT Collect after the April 2026 pivot; current coverage may slow",
            "the API is beta and requires a registered api.data.gov key",
        ]
        if lanes_capped:
            limitations.append(f"service lanes capped at {MAX_SERVICE_LANES}")
        if len(items) > cap:
            limitations.append(f"kept first {cap} of {len(items)} scoped contracts")
        return result(
            self.name, items=kept, total_matched=len(items), status=status,
            mode="live_official_api_server_scoped", limitations=limitations,
            attempts=attempts, url=API_ROOT,
            query={"service_ids": lanes, "limit": cap},
            receipt_payload=native_receipts,
            content_kind="application/json; profile=canonical-api-response",
        )


try:
    register_source(GsaItCollectSource)
except ValueError:
    pass
