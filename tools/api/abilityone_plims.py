"""AbilityOne PLIMS Procurement List adapter (keyless, official).

Client dimension: mandatory-source / vehicle access.  PLIMS identifies products
and services that ordering activities must buy through AbilityOne before a
normal competitive solicitation is considered.  The source contributes NSNs,
service locations, ordering organizations, and nonprofit/CNA identities.

The reports page offers full-list downloads, but the public search UI also uses
``getsearchservices`` routes with server-side filters.  This adapter uses those
bounded routes only: at most four query lanes, two result families per lane,
and a disclosed result cap.  It never bulk-mirrors the Procurement List.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

from tools.api._http import get_json, get_text
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.api.provenance import make_provenance_envelope

SOURCE_NAME = "abilityone_plims"
BASE_URL = "https://plims.abilityone.gov"
REPORTS_URL = f"{BASE_URL}/reports/"
SEARCH_URL = f"{BASE_URL}/getsearchservices/"
MAX_QUERY_LANES = 4
MAX_PAGE_SIZE = 50
MAX_RETURNED = 100


def _text(value: Any) -> Optional[str]:
    cleaned = " ".join(str(value or "").split())
    return cleaned or None


def _day(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def _stable_id(kind: str, *parts: Any) -> str:
    basis = "|".join(str(part or "").strip().casefold() for part in parts)
    return f"abilityone:{kind}:" + hashlib.sha256(
        basis.encode("utf-8")).hexdigest()[:20]


def _cna(value: Any) -> Optional[str]:
    text = _text(value)
    return "SourceAmerica" if text == "NISH" else text


def _product(row: dict, term: str) -> Optional[dict]:
    nsn = _text(row.get("plims_name") or row.get("NSN"))
    name = _text(row.get("nsn.plims_productname") or row.get("Name"))
    if not nsn and not name:
        return None
    project_id = _text(row.get("plims_productprojectnsnid"))
    return {
        "record_id": (
            f"abilityone:product:{project_id}" if project_id
            else _stable_id("product", nsn, name)
        ),
        "kind": "product",
        "nsn": nsn,
        "name": name,
        "description": _text(
            row.get("nsn.plims_productdescriptiontext")
            or row.get("Description")),
        "cna": _cna(row.get("project.plims_cna") or row.get("CNA")),
        "nonprofit_agency": _text(row.get("project.plims_npa")),
        "ordering_activity": _text(
            row.get("agency.plims_agencyname")
            or row.get("Mandatory for Contracting Activity")),
        "effective_date": _day(row.get("nsn.plims_activedate")),
        "list_type": _text(row.get("nsn.plims_listtype")),
        "matched_terms": [term],
        "url": f"{BASE_URL}/search-products/?nsn={quote(nsn or '')}",
    }


def _service(row: dict, term: str) -> Optional[dict]:
    service_type = _text(
        row.get("servicetypes.plims_name")
        or row.get("st.plims_shortdescription"))
    location = _text(
        row.get("plims_serviceprojectlocations.plims_locationname")
        or row.get("spl.plims_locationname"))
    activity = _text(
        row.get("plims_agencies.plims_agencyname")
        or row.get("ca.plims_name"))
    department = _text(
        row.get("plims_agencies.plims_department")
        or row.get("d.plims_name"))
    if not any((service_type, location, activity)):
        return None
    city = _text(
        row.get("plims_serviceprojectlocations.plims_city")
        or row.get("spl.plims_city"))
    state = _text(
        row.get("plims_serviceprojectlocations.plims_state")
        or row.get("spl.plims_state"))
    return {
        "record_id": _stable_id(
            "service", service_type, location, activity, department),
        "kind": "service",
        "service_type": service_type,
        "service_location": location,
        "city": city,
        "state": state,
        "cna": _cna(
            row.get("serviceprojects.plims_cna") or row.get("sp.plims_cna")),
        "ordering_activity": activity,
        "department": department,
        "effective_date": _day(
            row.get("serviceprojects.plims_transactioneffectivedate")),
        "matched_terms": [term],
        "url": f"{BASE_URL}/Search-Services/",
    }


def _lanes(query: SourceQuery) -> tuple[list[tuple[str, str]], bool]:
    raw: list[tuple[str, str]] = []
    raw.extend(("keyword", value) for value in query.keywords)
    raw.extend(("agency", value) for value in query.agencies)
    raw.extend(("nsn", value) for value in query.psc_codes)
    lanes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for lane, value in raw:
        term = str(value or "").strip().strip('"')
        key = (lane, term.casefold())
        if term and key not in seen:
            seen.add(key)
            lanes.append((lane, term))
    return lanes[:MAX_QUERY_LANES], len(lanes) > MAX_QUERY_LANES


def _params(kind: str, lane: str, term: str, page_size: int) -> dict:
    params: dict[str, Any] = {
        "type": f"search{kind}", "page": 1, "pageSize": page_size,
    }
    if kind == "products":
        if lane == "agency":
            params["plims_department"] = term
        elif lane == "nsn":
            params["plims_name"] = term
        else:
            params["plims_productdescriptiontext"] = term
            params["plims_productname"] = term
    else:
        if lane == "agency":
            params["plims_department"] = term
        else:
            params["plims_servicetype"] = term
    return params


class AbilityOnePlimsSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, fetch_json: Optional[Callable[..., Any]] = None) -> None:
        self._fetch_json = fetch_json or get_json

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError(
            "enrichment-only source; use procurement_list()")

    def healthcheck(self) -> tuple[bool, str]:
        try:
            html = get_text(REPORTS_URL, timeout=8.0, retries=1)
        except Exception as exc:  # noqa: BLE001
            return False, f"PLIMS reports page unreachable: {exc}"
        if "Procurement List Reports" in html:
            return True, "official PLIMS reports page reachable"
        return False, "reports page returned unexpected content"

    def procurement_list(self, query: SourceQuery) -> dict[str, Any]:
        lanes, lanes_capped = _lanes(query)
        if not lanes:
            return self._payload([], [], "partial", [
                "unscoped query refused; pass keywords, agencies, or PSC/NSN terms",
            ])

        page_size = max(1, min(query.limit, MAX_PAGE_SIZE))
        by_id: dict[str, dict] = {}
        attempts: list[dict] = []
        for lane, term in lanes:
            # NSN-shaped terms do not have a meaningful service translation.
            families = ("products",) if lane == "nsn" else ("products", "services")
            for family in families:
                try:
                    payload = self._fetch_json(
                        SEARCH_URL,
                        params=_params(family, lane, term, page_size),
                        timeout=20.0,
                        retries=2,
                    )
                    native = payload.get("results") if isinstance(payload, dict) else None
                    if not isinstance(native, list):
                        raise ValueError("PLIMS response has no results list")
                    normalized = []
                    for row in native[:page_size]:
                        if not isinstance(row, dict):
                            continue
                        item = (_product(row, term) if family == "products"
                                else _service(row, term))
                        if item:
                            normalized.append(item)
                            existing = by_id.get(item["record_id"])
                            if existing:
                                for matched in item["matched_terms"]:
                                    if matched not in existing["matched_terms"]:
                                        existing["matched_terms"].append(matched)
                            else:
                                by_id[item["record_id"]] = item
                    attempts.append({
                        "source": f"plims_{family}:{term}",
                        "status": "success",
                        "count": len(normalized),
                    })
                except Exception as exc:  # noqa: BLE001 - lane isolation
                    attempts.append({
                        "source": f"plims_{family}:{term}",
                        "status": "failed",
                        "count": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    })

        failures = sum(row["status"] == "failed" for row in attempts)
        status = (
            "failed" if failures == len(attempts)
            else "partial" if failures or lanes_capped
            else "complete"
        )
        limitations = [
            "search-route snapshot only; withdrawn Procurement List entries are not history",
            "PLIMS search endpoints are undocumented Power Pages routes and may drift",
        ]
        if lanes_capped:
            limitations.append(
                f"query lanes capped at {MAX_QUERY_LANES}; additional terms not attempted")
        if failures:
            limitations.append(
                f"{failures} PLIMS query lanes failed; successful lanes remain usable")
        return self._payload(list(by_id.values()), attempts, status, limitations)

    def _payload(self, items: list[dict], attempts: list[dict],
                 status: str, limitations: list[str]) -> dict:
        capped = cap_disclosed(items, MAX_RETURNED, key="items")
        retrieved_at = datetime.now(timezone.utc)
        capped.update({
            "source_url": REPORTS_URL,
            "search_url": SEARCH_URL,
            "_provenance": make_provenance_envelope(
                self.name,
                status=status,
                mode="live_bounded_server_search",
                retrieval_mode="live",
                retrieved_at=retrieved_at,
                record_count=len(capped["items"]),
                attempts=attempts,
                limitations=limitations,
            ).model_dump(mode="json"),
        })
        return capped


# Catalog-first registration: the integrator adds SourceSpec before importing
# this adapter into tools.api.  Keeping the guard makes fixture tests independent.
try:
    register_source(AbilityOnePlimsSource)
except ValueError:
    pass
