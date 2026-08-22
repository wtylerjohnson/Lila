"""SAM Entity Management + Exclusions adapter — partner vetting (existing key).

The Target stage recommends orgs to team with; this adapter answers the two
questions that must be asked before anyone reaches out:
  1. Is the org actually registered in SAM (active, with UEI/CAGE)?
  2. Is it excluded (debarred/suspended)? Teaming with an excluded party is a
     compliance failure a client will never forgive.

APIs (both on the SAM_GOV_API_KEY already in .env):
  Entity:     GET https://api.sam.gov/entity-information/v3/entities
                  ?api_key=...&legalBusinessName=X   (docs: open.gsa.gov/api/entity-api/)
  Exclusions: GET https://api.sam.gov/entity-information/v4/exclusions
                  ?api_key=...&exclusionName=X       (docs: open.gsa.gov/api/exclusions-api/)

Vetting NEVER blocks the pipeline: a failed lookup returns verdict='unverified'
with the reason, and the report shows that honestly instead of guessing.
"""

from __future__ import annotations

import os
from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

ENTITY_URL = "https://api.sam.gov/entity-information/v3/entities"
EXCLUSIONS_URL = "https://api.sam.gov/entity-information/v4/exclusions"


def _first_list(payload: dict) -> list[dict]:
    for v in (payload or {}).values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def _dig(rec: dict, *path: str) -> Any:
    cur: Any = rec
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


@register_source
class EntityVettingSource(DataSource):
    name = "entity_vetting"
    kind = SourceKind.ENRICHMENT

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("SAM_GOV_API_KEY")

    def healthcheck(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "SAM_GOV_API_KEY missing"
        return True, "key set (2 calls per vetted org, spent at Target stage)"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use vet()/vet_many()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Registry-compatible entry point: vets query.keywords as org names."""
        return self.vet_many(query.keywords or [])

    def vet(self, org_name: str) -> dict[str, Any]:
        """One org -> registration + exclusion verdict, failure-honest."""
        if not self._api_key:
            return {"org": org_name, "verdict": "unverified",
                    "detail": "SAM_GOV_API_KEY not set"}
        out: dict[str, Any] = {"org": org_name}
        import tools.api.sam_quota as sam_quota

        try:
            sam_quota.note_call("entity")
            ent = get_json(ENTITY_URL, params={
                "api_key": self._api_key, "legalBusinessName": org_name,
            }, timeout=15.0, retries=2)
            recs = _first_list(ent)
            if recs:
                r = recs[0]
                reg = r.get("entityRegistration") or r
                out["registered"] = True
                out["registration_status"] = _dig(reg, "registrationStatus") or reg.get("registrationStatus")
                out["uei"] = reg.get("ueiSAM") or _dig(r, "entityRegistration", "ueiSAM")
                out["cage"] = reg.get("cageCode")
                out["legal_name"] = reg.get("legalBusinessName") or org_name
            else:
                out["registered"] = False
        except Exception as e:  # noqa: BLE001
            out["registered"] = None
            out["entity_error"] = str(e)

        try:
            sam_quota.note_call("exclusions")
            exc = get_json(EXCLUSIONS_URL, params={
                "api_key": self._api_key, "exclusionName": org_name,
            }, timeout=15.0, retries=2)
            hits = _first_list(exc)
            out["excluded"] = bool(hits)
            if hits:
                out["exclusion_detail"] = [
                    {"name": _dig(h, "exclusionDetails", "exclusionType")
                             or h.get("exclusionType"),
                     "agency": _dig(h, "excludingAgency") or h.get("excludingAgencyName")}
                    for h in hits[:3]
                ]
        except Exception as e:  # noqa: BLE001
            out["excluded"] = None
            out["exclusion_error"] = str(e)

        if out.get("excluded"):
            out["verdict"] = "EXCLUDED"
        elif out.get("registered") and out.get("excluded") is False:
            out["verdict"] = "vetted"
        elif out.get("registered") is False:
            out["verdict"] = "not_registered"
        else:
            out["verdict"] = "unverified"
            out.setdefault("detail", out.get("entity_error") or out.get("exclusion_error"))
        return out

    def vet_many(self, org_names: list[str], limit: int = 10) -> dict[str, Any]:
        """Vet up to `limit` unique orgs (2 API calls each — rate-limit aware)."""
        seen: list[str] = []
        for n in org_names:
            n = (n or "").strip()
            if n and n.lower() not in [s.lower() for s in seen]:
                seen.append(n)
        results = {n: self.vet(n) for n in seen[:limit]}
        return {"orgs": results, "vetted_count": len(results),
                "skipped": max(0, len(seen) - limit)}
