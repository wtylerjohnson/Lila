"""NIH NITAAC contract-holder CSV adapter.

Holder rows prove ordering-vehicle access and task-area coverage.  They never
prove incumbency on an agency requirement or the existence of a live deal.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Callable, Optional

from tools.api import _http
from tools.api._connector_wave3 import (failure, matches, result, scoped_terms,
                                        stable_id, text)
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "nitaac_holders"
EXPORT_URL = ("https://nitaac.nih.gov/search/contract-holders/export?"
              "f%5B0%5D=gwac%3A6&_format=csv")
MAX_ROWS = 500


def _split(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _parse_csv(blob: bytes) -> list[dict[str, Any]]:
    decoded = blob.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))
    required = {"Contract Holder", "PIID(s)", "SAM UID", "Task Areas"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("NITAAC CSV header drifted; required holder keys are absent")
    rows: list[dict[str, Any]] = []
    for native in reader:
        holder = text(native.get("Contract Holder"))
        piids = _split(native.get("PIID(s)"))
        uei = text(native.get("SAM UID"))
        if not holder or not (piids or uei):
            continue
        rows.append({
            "record_id": stable_id(SOURCE_NAME, uei, *piids, holder),
            "holder_name": holder,
            "piids": piids,
            "uei": uei,
            "duns": text(native.get("DUNS Number")),
            "task_areas": _split(native.get("Task Areas")),
            "business_size": text(native.get("Business Size")),
            "business_type": text(native.get("Business Type")),
            "contract_types": _split(native.get("Contract Type(s)")),
            "program_manager": text(native.get("Program Manager")),
            "program_manager_email": text(native.get("PM Email Address")),
            "program_manager_phone": text(native.get("PM Phone Number")),
            "contract_url": text(native.get("Contract URL")),
            "_join_keys": {"uei": uei, "parent_award_piids": piids},
            "evidence_semantics": "vehicle_access_not_incumbency",
        })
    return rows


class NitaacHoldersSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetch_bytes: Optional[Callable[..., bytes]] = None) -> None:
        self._fetch_bytes = fetch_bytes or self._live_bytes

    @staticmethod
    def _live_bytes(url: str, **kwargs: Any) -> bytes:
        with _http._client(float(kwargs.get("timeout", 30.0))) as client:
            response = client.get(url)
        response.raise_for_status()
        return bytes(response.content)

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use contract_holders()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            rows = _parse_csv(self._fetch_bytes(EXPORT_URL, timeout=15.0))
            return bool(rows), f"official NITAAC holder CSV reachable ({len(rows)} rows)"
        except Exception as exc:  # noqa: BLE001
            return False, f"NITAAC holder CSV unavailable: {exc}"

    def contract_holders(self, query: SourceQuery) -> dict[str, Any]:
        terms = scoped_terms(query)
        if not terms:
            return result(
                self.name, items=[], status="partial", mode="refused_unscoped",
                url=EXPORT_URL,
                limitations=["unscoped roster pull refused; pass a holder, UEI, PIID, task area, or vehicle term"],
            )
        try:
            blob = self._fetch_bytes(EXPORT_URL, timeout=30.0)
            native = _parse_csv(blob)
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, mode="live_official_csv", url=EXPORT_URL,
                           error=exc)
        matched = [row for row in native if matches(row, terms)]
        cap = max(1, min(query.limit, MAX_ROWS))
        kept = matched[:cap]
        limitations = [
            "holder status proves NITAAC vehicle access, not incumbency or buyer preference",
            "CIO-SP3-family ordering is in transition; this is not an evergreen opportunity feed",
        ]
        if len(matched) > cap:
            limitations.append(f"kept first {cap} of {len(matched)} scoped matches")
        return result(
            self.name, items=kept, total_matched=len(matched),
            status="partial" if len(matched) > cap else "complete",
            mode="live_official_csv_scoped_local_filter", limitations=limitations,
            url=EXPORT_URL, query={"terms": terms, "limit": cap}, raw=blob,
            content_kind="text/csv",
        )


try:
    register_source(NitaacHoldersSource)
except ValueError:
    pass
