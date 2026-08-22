"""SAM Federal Hierarchy adapter — the government's org chart (SAM key).

Notices name a department; deals close with an OFFICE. The Federal Hierarchy
API resolves agency names into their sub-tier organizations and office IDs —
feeding the Target stage the actual buying offices instead of "DHS".

API (docs: https://open.gsa.gov/api/fh-public-api/):
    GET https://api.sam.gov/prod/federalorganizations/v1/orgs
        ?api_key=...&fhorgname=<name>&limit=N
Same api.sam.gov daily quota as the opportunities key — every call is metered
in the SAM ledger (purpose 'hierarchy').
"""

from __future__ import annotations

import os
from typing import Any

import tools.api.sam_quota as sam_quota
from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

ORGS_URL = os.environ.get(
    "LILA_FH_URL", "https://api.sam.gov/prod/federalorganizations/v1/orgs")


def _orglist(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("orglist", "orgList", "content", "data"):
            if key not in payload:
                continue
            rows = payload[key]
            if not isinstance(rows, list):
                raise ValueError(
                    f"SAM Federal Hierarchy response field {key} was not a list"
                )
            return _validate_orgs(rows)
    if isinstance(payload, list):
        return _validate_orgs(payload)
    raise ValueError(
        "SAM Federal Hierarchy response omitted a recognized organization list"
    )


def _validate_orgs(rows: list[Any]) -> list[dict]:
    """Require the name and stable hierarchy ID that identify an organization."""
    orgs: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(
                "SAM Federal Hierarchy response contained a non-object row"
            )
        name = row.get("fhorgname") or row.get("fhOrgName")
        org_id = row.get("fhorgid") or row.get("fhOrgId")
        if not str(name or "").strip():
            raise ValueError("SAM Federal Hierarchy row omitted organization name")
        if org_id is None or not str(org_id).strip():
            raise ValueError("SAM Federal Hierarchy row omitted organization ID")
        orgs.append(row)
    return orgs


@register_source
class FederalHierarchySource(DataSource):
    name = "federal_hierarchy"
    kind = SourceKind.ENRICHMENT

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("SAM_GOV_API_KEY")

    def healthcheck(self) -> tuple[bool, str]:
        # No live probe: shares the scarce SAM daily quota.
        if not self._api_key:
            return False, "SAM_GOV_API_KEY missing"
        return True, f"key set (metered) · {sam_quota.summary()}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Sub-organizations per target agency: {agency: [org, ...]}.

        Costs ONE metered SAM call per agency, capped at 3 per run.
        """
        if not self._api_key:
            raise RuntimeError("SAM_GOV_API_KEY not set")
        out: dict[str, Any] = {}
        # One agency, not three: see the pool arithmetic in sam_quota.
        for agency in (query.agencies or [])[:1]:
            sam_quota.note_call("hierarchy")
            payload = get_json(ORGS_URL, params={
                "api_key": self._api_key, "fhorgname": agency, "limit": 10,
            })
            orgs = []
            for o in _orglist(payload):
                orgs.append({
                    "name": o.get("fhorgname") or o.get("fhOrgName"),
                    "type": o.get("fhorgtype") or o.get("fhOrgType"),
                    "id": o.get("fhorgid") or o.get("fhOrgId"),
                    "status": o.get("status"),
                    "agency_code": o.get("agencycode") or o.get("cgac"),
                })
            if orgs:
                out[agency] = orgs
        return out
