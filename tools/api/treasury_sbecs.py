"""Treasury Dynamic Forecast (SBECS) guest-Apex adapter.

The public Salesforce LWR application exposes guest read methods.  This
adapter pins those method names, resolves query filters through the official
lookup methods, and never treats forecast intent as RawOpportunity discovery.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from tools.api._connector_wave3 import (failure, matches, result, stable_id,
                                        text)
from tools.api._http import post_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SOURCE_NAME = "treasury_sbecs"
LANDING_URL = "https://sbecs.treasury.gov/"
APEX_URL = ("https://sbecs.treasury.gov/webruntime/api/apex/execute?"
            "language=en-US&asGuest=true&htmlEncode=false")
LOOKUP_CLASS = "SbfFilterLookupsWithoutSharing"
DATA_CLASS = "sbfPortalController"
MAX_RETURNED = 250


def _payload(classname: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"namespace": "", "classname": classname, "method": method,
            "isContinuation": False, "params": params, "cacheable": False}


def _naics(native: dict[str, Any]) -> Optional[str]:
    value = ((native.get("sbfNAICSCode__r") or {}).get("SBF_NAICS_Description__c")
             if isinstance(native.get("sbfNAICSCode__r"), dict) else None)
    match = re.match(r"(\d{6})", text(value) or "")
    return match.group(1) if match else None


def _psc(native: dict[str, Any]) -> Optional[str]:
    value = ((native.get("sbfPSCCode__r") or {}).get("sbfPSCCodeandDefinition__c")
             if isinstance(native.get("sbfPSCCode__r"), dict) else None)
    match = re.match(r"([A-Z0-9]{4})", (text(value) or "").upper())
    return match.group(1) if match else None


def _records(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    categories = {
        "newOppData": "new", "recompeteOppData": "recompete",
        "micropurchaseAwardData": "micropurchase_planning",
        "simplifiedAcquisitionAwardData": "simplified_acquisition_planning",
        "above250kData": "above_250k_planning",
    }
    for group in groups:
        bureau = text(group.get("Name"))
        for key, category in categories.items():
            values = group.get(key) or []
            if not isinstance(values, list):
                continue
            for native in values:
                if not isinstance(native, dict) or not text(native.get("Name")):
                    continue
                native_id = text(native.get("Id"))
                record_id = (f"treasury-sbecs:{native_id}" if native_id else
                             stable_id(SOURCE_NAME, bureau, native.get("Name"),
                                       native.get("sbfProjectedAwardFY_Qtr__c")))
                out[record_id] = {
                    "record_id": record_id,
                    "title": text(native.get("Name")),
                    "bureau": bureau,
                    "planning_category": category,
                    "requirement_type": text(native.get("sbfTypeofRequirement__c")),
                    "status": text(native.get("sbfAcquisitionPhase__c")),
                    "naics_code": _naics(native),
                    "psc_code": _psc(native),
                    "contract_number": text(native.get("sbfContractNumber__c")),
                    "incumbent_vendor": text(native.get("sbfIncumbentVendorName__c")),
                    "program_office": text(
                        (native.get("sbfProgramOffice__r") or {}).get("Name")
                        if isinstance(native.get("sbfProgramOffice__r"), dict) else None),
                    "projected_award_fy_quarter": text(
                        native.get("sbfProjectedAwardFY_Qtr__c")
                        or native.get("sbfFiscalYear_QtrforAward__c")),
                    "estimated_value_band": text(
                        native.get("sbfEstimatedTotalContractValue__c")
                        or native.get("sbfTotalContractValue__c")),
                    "set_aside": text(native.get("sbfTypeofSmallBusinessSetaside__c")
                                      or native.get("sbfTypeofSBSA__c")),
                    "last_modified": text(native.get("SystemModstamp")),
                    "_join_keys": {"salesforce_record_id": native_id,
                                   "incumbent_contract_number": text(native.get("sbfContractNumber__c")),
                                   "naics": _naics(native), "psc": _psc(native)},
                    "evidence_semantics": "planning_intent_not_live_opportunity",
                }
    return list(out.values())


class TreasurySbecsSource(DataSource):
    name = SOURCE_NAME
    kind = SourceKind.ENRICHMENT

    def __init__(self, apex: Optional[Callable[[str, str, dict[str, Any]], Any]] = None) -> None:
        self._apex_call = apex or self._live_apex

    @staticmethod
    def _live_apex(classname: str, method: str, params: dict[str, Any]) -> Any:
        response = post_json(APEX_URL, json=_payload(classname, method, params),
                             timeout=30.0, retries=2)
        if not isinstance(response, dict) or "returnValue" not in response:
            raise ValueError("SBECS Apex response has no returnValue")
        return response["returnValue"]

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("forecast evidence is enrichment-only; use forecast_records()")

    def _lookup(self, method: str) -> list[dict[str, Any]]:
        value = self._apex_call(LOOKUP_CLASS, method, {})
        if not isinstance(value, list):
            raise ValueError(f"{method} returned no lookup list")
        return [row for row in value if isinstance(row, dict)]

    def healthcheck(self) -> tuple[bool, str]:
        try:
            value = self._apex_call(LOOKUP_CLASS, "getListForFilter_Bureau", {})
            return isinstance(value, list) and bool(value), (
                f"official SBECS guest Apex reachable ({len(value)} bureaus)")
        except Exception as exc:  # noqa: BLE001
            return False, f"SBECS guest read unavailable: {exc}"

    def forecast_records(self, query: SourceQuery) -> dict[str, Any]:
        has_server_scope = bool(query.agencies or query.naics_codes or query.psc_codes
                                or query.posted_from or query.posted_to)
        if not has_server_scope:
            return result(
                self.name, items=[], status="partial", mode="refused_unscoped",
                url=LANDING_URL,
                limitations=["unscoped SBECS pull refused; pass a bureau, NAICS, PSC, or fiscal-year window"],
            )
        try:
            filters: dict[str, list[str]] = {
                "bureauFilter": [], "NAICSFilter": [], "PSCFilter": [],
                "fiscalYearFilter": [], "setAsideFilter": [],
                "periodOfPerformanceFilter": [], "totalContractValueFilter": [],
                "vendorNameFilter": [], "placeOfPerformanceFilter": [],
                "opportunityTypeFilter": [],
            }
            if query.agencies:
                wanted = " ".join(query.agencies).casefold()
                filters["bureauFilter"] = [
                    str(row["Key"]) for row in self._lookup("getListForFilter_Bureau")
                    if text(row.get("Value")) and text(row.get("Value")).casefold() in wanted
                ]
            if query.naics_codes:
                requested = tuple(str(code) for code in query.naics_codes)
                filters["NAICSFilter"] = [
                    str(row["Key"]) for row in self._lookup("getListForFilter_NAICS")
                    if any((text(row.get("Value")) or "").startswith(code) for code in requested)
                ]
            if query.psc_codes:
                requested_psc = tuple(str(code).upper() for code in query.psc_codes)
                filters["PSCFilter"] = [
                    str(row["Key"]) for row in self._lookup("getListForFilter_Psc")
                    if any((text(row.get("Value")) or "").upper().startswith(code)
                           for code in requested_psc)
                ]
            if query.posted_from or query.posted_to:
                low = (query.posted_from or query.posted_to).year
                high = (query.posted_to or query.posted_from).year
                filters["fiscalYearFilter"] = [
                    f"FY {year} Q{quarter}" for year in range(low, high + 1)
                    for quarter in range(1, 5)
                ]
            groups = self._apex_call(DATA_CLASS, "getData", filters)
            if not isinstance(groups, list):
                raise ValueError("sbfPortalController.getData returned no group list")
            native = _records([row for row in groups if isinstance(row, dict)])
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, mode="live_guest_apex_scoped", url=APEX_URL,
                           error=exc)
        matched = [row for row in native if matches(row, query.keywords)]
        cap = max(1, min(query.limit, MAX_RETURNED))
        kept = matched[:cap]
        limitations = [
            "SBECS is planning-only and is neither a presolicitation synopsis nor a commitment",
            "the guest Apex contract is undocumented and protected by schema checks",
            "incumbent-name joins are inference-grade unless a contract number independently resolves",
        ]
        if len(matched) > cap:
            limitations.append(f"kept first {cap} of {len(matched)} server-scoped matches")
        return result(
            self.name, items=kept, total_matched=len(matched),
            status="partial" if len(matched) > cap else "complete",
            mode="live_guest_apex_server_scoped", limitations=limitations,
            url=APEX_URL, query=filters,
            receipt_payload=groups,
            content_kind="application/json; profile=canonical-apex-response",
        )


try:
    register_source(TreasurySbecsSource)
except ValueError:
    pass
