"""DOJ public Power BI Gov forecast adapter.

The adapter replays the publisher's active-opportunities table query with its
native 500-row window and decodes Power BI's dictionary-compressed response.
Rows remain planning evidence and never enter RawOpportunity discovery.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Optional

from tools.api import _http
from tools.api._connector_wave3 import (failure, matches, result, scoped_terms,
                                        text)
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "doj_forecast"
#: THE CITATION AND THE RETRIEVAL PATH ARE DIFFERENT THINGS. The forecast
#: lives in a Power BI Government workspace, which is a Microsoft-operated
#: cloud rather than a justice.gov page. A client artifact cites the AGENCY
#: that owns the data; the vendor embed stays as the retrieval path in
#: provenance. Verified live 2026-08-08: the OSDBU root answers 200 while
#: the forecast sub-path this adapter's catalog row named answers 404.
LANDING_URL = "https://www.justice.gov/osdbu"

EMBED_URL = ("https://app.high.powerbigov.us/view?"
             "r=eyJrIjoiMzdhNTVmNTQtYmM5MS00MmQ4LTg1OGYtOTE5YThiNmFmYzE5IiwidCI6IjE1ZWYxMmExLWFmNTgtNDRjNC1iMDI5LTcxMmZjMDYwNTU3MCJ9")
MAX_NATIVE_ROWS = 500
MAX_RETURNED = 250
_RESOURCE_RE = re.compile(r'resourceDescriptor\s*=\s*JSON\.parse\(\'([^\']+)')
_CLUSTER_RE = re.compile(r"resolvedClusterUri\s*=\s*'([^']+)'")
_ACTIVITY_RE = re.compile(r"telemetrySessionId\s*=\s*'([^']+)'")


def _api_cluster(uri: str) -> str:
    head, tail = uri.rstrip("/").split("//", 1)
    parts = tail.split(".")
    parts[0] = parts[0].replace("-redirect", "").replace("global-", "") + "-api"
    return head + "//" + ".".join(parts)


def _table_visual(metadata: dict[str, Any]) -> dict[str, Any]:
    for section in metadata.get("exploration", {}).get("sections", []):
        if section.get("displayName") != "Opportunities List":
            continue
        for visual in section.get("visualContainers", []):
            config = json.loads(visual.get("config") or "{}")
            if config.get("singleVisual", {}).get("visualType") == "tableEx" and visual.get("query"):
                return visual
    raise ValueError("DOJ Power BI active-opportunities table visual not found")


def _decode_query(response: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    data = response["results"][0]["result"]["data"]
    descriptors = data["descriptor"]["Select"]
    ds = data["dsr"]["DS"][0]
    rows = ds["PH"][0]["DM0"]
    dictionaries = ds.get("ValueDicts", {})
    name_by_value: dict[str, str] = {}
    for descriptor in descriptors:
        value = descriptor.get("Value")
        if value and value not in name_by_value:
            name_by_value[value] = descriptor["Name"].split(".", 1)[-1]
    first = rows[0].get("S", []) if rows else []
    columns = [entry.get("N", f"G{i}") for i, entry in enumerate(first)]
    if not columns:
        columns = [f"G{i}" for i in range(len(dictionaries))]
    previous: list[Any] = [None] * len(columns)
    decoded: list[dict[str, Any]] = []
    for native in rows:
        cells = iter(native.get("C", []))
        repeat_mask = int(native.get("R", 0))
        values: list[Any] = []
        for index in range(len(columns)):
            if repeat_mask & (1 << index):
                value = previous[index]
            else:
                value = next(cells, None)
                dictionary = dictionaries.get(f"D{index}")
                if isinstance(dictionary, list) and isinstance(value, int) and value < len(dictionary):
                    value = dictionary[value]
            values.append(value)
        previous = values
        decoded.append({name_by_value.get(column, column): value
                        for column, value in zip(columns, values)})
    return decoded, len(rows) >= MAX_NATIVE_ROWS


def _normalize(native: dict[str, Any]) -> Optional[dict[str, Any]]:
    action = text(native.get("Action Tracking Number"))
    title = text(native.get("Contract Name") or native.get("Description of Requirement"))
    if not action or not title:
        return None
    naics = (text(native.get("NAICS Code")) or "").split("--", 1)[0] or None
    psc = (text(native.get("Product Service Code")) or "").split("---", 1)[0] or None
    return {
        "record_id": f"doj-forecast:{action.upper()}",
        "action_tracking_number": action,
        "title": title,
        "description": text(native.get("Description of Requirement")),
        "contracting_office": text(native.get("Contracting Office")),
        "naics_code": naics,
        "psc_code": psc,
        "target_solicitation": text(native.get("Target Solicitation Date")),
        "target_award": text(native.get("Target Award Date")),
        "estimated_value_band": text(native.get("Estimated Total Contract Value (Range)")),
        "incumbent_contractor": text(native.get("Incumbent Contractor")),
        "incumbent_piid": text(native.get("Incumbent Contractor PIID")),
        "requirement_poc_email": text(native.get("DOJ Requirement POC - Email Address")),
        "requirement_poc_name": text(native.get("DOJ Requirement POC - Name")),
        "fiscal_year": text(native.get("Fiscal Year")),
        "_join_keys": {"action_tracking_number": action,
                       "contracting_office_code": (text(native.get("Contracting Office")) or "").split("--", 1)[0] or None,
                       "incumbent_piid": text(native.get("Incumbent Contractor PIID")),
                       "naics": naics, "psc": psc},
        "evidence_semantics": "planning_intent_not_live_opportunity",
    }


def _live_rows() -> tuple[list[dict[str, Any]], bool]:
    with _http._client(45.0) as client:
        landing = client.get(EMBED_URL)
        landing.raise_for_status()
        resource_m = _RESOURCE_RE.search(landing.text)
        cluster_m = _CLUSTER_RE.search(landing.text)
        activity_m = _ACTIVITY_RE.search(landing.text)
        if not all((resource_m, cluster_m, activity_m)):
            raise ValueError("DOJ Power BI bootstrap fields absent")
        # The descriptor is a JSON string embedded inside a JavaScript string,
        # so quotes are backslash-escaped once by the landing page.
        resource_json = resource_m.group(1).replace('\\"', '"')
        resource = json.loads(resource_json)["k"]
        cluster = _api_cluster(cluster_m.group(1))
        activity = activity_m.group(1)
        headers = {"Accept": "application/json", "ActivityId": activity,
                   "RequestId": str(uuid.uuid4()), "X-PowerBI-ResourceKey": resource}
        metadata_response = client.get(
            f"{cluster}/public/reports/{resource}/modelsAndExploration?preferReadOnlySession=true",
            headers=headers)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        visual = _table_visual(metadata)
        body = {"version": "1.0.0", "queries": [{"Query": json.loads(visual["query"])}],
                "cancelQueries": [], "modelId": metadata["models"][0]["id"]}
        headers.update({"RequestId": str(uuid.uuid4()), "Content-Type": "application/json"})
        query_response = client.post(
            f"{cluster}/public/reports/querydata?synchronous=true",
            headers=headers, json=body)
        query_response.raise_for_status()
        native, capped = _decode_query(query_response.json())
        return [row for row in (_normalize(value) for value in native) if row], capped


class DojForecastSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetch_rows: Optional[Callable[[], tuple[list[dict[str, Any]], bool]]] = None) -> None:
        self._fetch_rows = fetch_rows or _live_rows

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("forecast evidence is enrichment-only; use forecast_records()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            rows, capped = self._fetch_rows()
            suffix = "; native 500-row window reached" if capped else ""
            return bool(rows), f"DOJ public Power BI forecast reachable ({len(rows)} rows{suffix})"
        except Exception as exc:  # noqa: BLE001
            return False, f"DOJ forecast unavailable: {exc}"

    def forecast_records(self, query: SourceQuery) -> dict[str, Any]:
        terms = scoped_terms(query)
        if not terms:
            return result(self.name, items=[], status="partial", mode="refused_unscoped",
                          url=LANDING_URL,
                          limitations=["unscoped Power BI pull refused; pass action number, office, keyword, NAICS, or PSC"])
        try:
            native, native_capped = self._fetch_rows()
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, mode="live_public_powerbi_bounded", url=EMBED_URL,
                           error=exc)
        matched = [row for row in native if matches(row, terms)]
        cap = max(1, min(query.limit, MAX_RETURNED))
        kept = matched[:cap]
        limits = [
            "DOJ dashboard rows are forecasts/planning intent, not live solicitations",
            "publish-to-web keys and model schema can rotate; both are rediscovered and schema-checked each pull",
            "forward SAM joins remain fuzzy unless a solicitation link or incumbent PIID is stated",
        ]
        if native_capped:
            limits.append("publisher's active table reached its native 500-row window; results are partial")
        return result(
            self.name, items=kept, total_matched=len(matched),
            status="partial" if native_capped or len(matched) > cap else "complete",
            mode="live_public_powerbi_active_table_bounded", limitations=limits,
            url=LANDING_URL, query={"terms": terms, "limit": cap},
            receipt_payload={"rows": native, "native_window_capped": native_capped},
            content_kind="application/json; profile=canonical-powerbi-table",
        )


try:
    register_source(DojForecastSource)
except ValueError:
    pass
