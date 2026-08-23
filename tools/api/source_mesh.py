"""Scoped collection for verified enrichment sources inside the sweep.

This is the ingestion seam the source gauntlet was missing.  Every child read
is isolated, query-bound, hash-receipted, and persisted inside the designated
sweep.  Report presses consume the stored payload and never re-open a source.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from tools.api.base import REGISTRY, SourceQuery, SourceRegistry
from tools.api.provenance import make_provenance_envelope, provenance_from_payload
from tools.api.source_catalog import source_spec
from tools.toggles import is_enabled


SCHEMA_VERSION = 1
NORMALIZATION_VERSION = "source_mesh.v1.2026-08-08"
SOURCE_IDS = (
    "gsa_elibrary",
    "oasis_plus_holders",
    "fpds_atom",
    "sba_mentor_protege",
    "sba_sbs",
    "nih_reporter",
    "omb_apportionment",
    "abilityone_plims",
    "omb_public_budget_database",
    "cbca_cda_decisions",
    "ecfr_title48",
    "omb_sf133",
    "sba_procurement_scorecards",
    "hhs_oig_leie",
    "oversight_gov_reports",
    "doe_eere_exchange",
    "sam_wage_determinations",
    "epa_forecast",
    "nitaac_holders",
    "nasa_sewp_vi",
    "cofc_docket_rss",
    "treasury_sbecs",
    "doj_forecast",
    "gsa_it_collect",
    # Always-swept market lanes (operator order, 2026-08-18).
    "sam_mirrors",
    "esi_reseller_catalogs",
    "vendor_press",
)

_RESEARCH_BUYERS = (
    "nih", "national institutes of health", "niaid", "nci", "nhlbi",
    "nimh", "ninds", "niddk", "nia ", "fda", "cdc", "barda", "aspr",
    "ahrq", "hrsa",
)
_RESEARCH_WORK = (
    "clinical trial", "biomedical", "research", "laboratory", "preclinical",
    "vaccine", "therapeutic", "diagnostic", "genomic", "epidemiolog",
    "biospecimen", "assay", "clinical study", "drug development",
    "medical countermeasure",
)


def _nih_research_applicable(query: SourceQuery) -> bool:
    """Require both an NIH-family buyer and research work before retrieval."""
    blob = " ".join([
        *(str(value) for value in (query.agencies or ())),
        *(str(value) for value in (query.keywords or ())),
    ]).casefold()
    return (
        any(marker in blob for marker in _RESEARCH_BUYERS)
        and any(marker in blob for marker in _RESEARCH_WORK)
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")


def _count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    for key in (
        "holders", "actions", "pairs", "firms", "projects", "rows",
        "records", "items", "results", "accounts", "decisions", "sections",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    for key in ("record_count", "count", "total_matched"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
    return 0


def _invoke(source_id: str, source: Any, query: SourceQuery) -> Any:
    if source_id == "gsa_elibrary":
        return source.vehicle_holders(query)
    if source_id == "oasis_plus_holders":
        return source.holders(query)
    if source_id == "fpds_atom":
        return source.recent_actions(query, days=7)
    if source_id == "sba_mentor_protege":
        return source.enrich(query)
    if source_id == "sba_sbs":
        return source.firms(query)
    if source_id == "nih_reporter":
        return source.enrich(query)
    if source_id == "omb_apportionment":
        if not query.agencies:
            return {"not_run": "no agency scope for account-level join"}
        return source.apportionments(query)
    if source_id == "abilityone_plims":
        return source.procurement_list(query)
    if source_id == "omb_public_budget_database":
        return source.budget_authority(query)
    if source_id == "cbca_cda_decisions":
        return source.decisions(query)
    if source_id == "ecfr_title48":
        return source.regulations(query)
    if source_id == "omb_sf133":
        return source.budget_execution(query)
    if source_id == "sba_procurement_scorecards":
        return source.scorecards(query)
    if source_id == "hhs_oig_leie":
        return source.screen(query)
    if source_id == "oversight_gov_reports":
        return source.reports(query)
    if source_id == "doe_eere_exchange":
        return source.announcements(query)
    if source_id == "sam_wage_determinations":
        return source.wage_determinations(query)
    if source_id == "epa_forecast":
        return source.forecast_records(query)
    if source_id == "nitaac_holders":
        return source.contract_holders(query)
    if source_id == "nasa_sewp_vi":
        return source.awardees(query)
    if source_id == "cofc_docket_rss":
        return source.litigation_events(query)
    if source_id == "treasury_sbecs":
        return source.forecast_records(query)
    if source_id == "doj_forecast":
        return source.forecast_records(query)
    if source_id == "gsa_it_collect":
        return source.portfolio_context(query)
    if source_id == "sam_mirrors":
        return source.category_sweep(query)
    if source_id == "esi_reseller_catalogs":
        return source.agreements(query)
    if source_id == "vendor_press":
        return source.press_items(query)
    raise KeyError(source_id)


def _receipt(
    source_id: str,
    payload: Any,
    query: SourceQuery,
    *,
    elapsed_s: float = 0.0,
) -> dict:
    now = datetime.now(timezone.utc)
    envelope = provenance_from_payload(payload, source=source_id)
    raw_provenance = (
        payload.get("_provenance")
        if isinstance(payload, dict)
        and isinstance(payload.get("_provenance"), dict)
        else {}
    )
    if isinstance(payload, dict) and payload.get("not_run"):
        status = "not-run"
    elif envelope is not None and envelope.status == "failed":
        status = "failed"
    elif envelope is not None and (
        envelope.status == "partial" or envelope.stale
    ):
        # Adapter provenance is authoritative. Several fail-soft adapters use
        # an ``error`` detail to explain a refused/not-applicable scoped read;
        # that must not overwrite their explicit partial verdict.
        status = "partial"
    elif isinstance(payload, dict) and payload.get("error"):
        status = "failed"
    elif isinstance(payload, dict) and (
        payload.get("partial") is True
        or payload.get("complete") is False
        or payload.get("errors")
        or payload.get("failed_terms")
        or payload.get("omitted_terms")
        or payload.get("skipped_terms")
        or (
            payload.get("truncated") is True
            and payload.get("boundary_complete") is not True
        )
    ):
        status = "partial"
    else:
        status = "success"
    retrieved_at = (
        envelope.retrieved_at.isoformat()
        if envelope is not None and envelope.retrieved_at else now.isoformat()
    )
    query_payload = query.model_dump(mode="json")
    content_bytes = _canonical_bytes(payload)
    spec = source_spec(source_id)
    retrieval_url = str(raw_provenance.get("retrieval_url") or spec.official_url)
    if isinstance(payload, dict):
        for key in (
            "source_url", "list_url", "xlsx_url", "workbook_url",
            "feed_url", "api_url",
            "search_url", "index_url", "document_page", "landing_url",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                retrieval_url = value
                break
    raw_sha = raw_provenance.get("raw_content_sha256")
    raw_kind = raw_provenance.get("raw_content_kind")
    normalized_sha = hashlib.sha256(content_bytes).hexdigest()
    return {
        "source_id": source_id,
        "status": status,
        "authoritative_url": spec.official_url,
        "retrieval_url": retrieval_url,
        "retrieval_method": "adapter-scoped request",
        "retrieval_query": query_payload,
        "retrieved_at": retrieved_at,
        "elapsed_s": round(max(0.0, elapsed_s), 3),
        "record_count": _count(payload),
        "content_sha256": normalized_sha,
        "content_kind": "normalized-source-payload",
        "raw_content_sha256": raw_sha,
        "raw_content_kind": raw_kind,
        # Required binding for every persisted payload. Exact source bytes win;
        # legacy adapters without them use a canonical adapter-envelope receipt,
        # which is explicitly distinguished from a raw-response hash.
        "immutable_receipt_sha256": raw_sha or normalized_sha,
        "immutable_receipt_kind": (
            "raw-source-response" if raw_sha
            else "canonical-adapter-envelope-equivalent"
        ),
        "normalization_version": NORMALIZATION_VERSION,
        "payload": payload,
    }


def collect(
    query: SourceQuery,
    *,
    client_name: str,
    scope_designator: str,
    registry: SourceRegistry = REGISTRY,
) -> dict:
    """Collect every enabled child; one failure never sinks another."""
    sources: dict[str, dict] = {}
    futures = {}
    strict_offline = (
        os.environ.get("LILA_SUITE_OFFLINE", "").strip().casefold()
        in {"1", "true", "yes", "on"}
    )
    with ThreadPoolExecutor(max_workers=len(SOURCE_IDS)) as executor:
        for source_id in SOURCE_IDS:
            if source_id == "nih_reporter" and not _nih_research_applicable(query):
                sources[source_id] = {
                    "source_id": source_id,
                    "status": "not-run",
                    "authoritative_url": source_spec(source_id).official_url,
                    "retrieval_query": query.model_dump(mode="json"),
                    "record_count": 0,
                    "normalization_version": NORMALIZATION_VERSION,
                    "not_run": (
                        "NIH research contracts require both an NIH-family "
                        "buyer and research-work terms"
                    ),
                }
                continue
            if not is_enabled(source_id, True):
                sources[source_id] = {
                    "source_id": source_id,
                    "status": "not-run",
                    "authoritative_url": source_spec(source_id).official_url,
                    "retrieval_query": query.model_dump(mode="json"),
                    "record_count": 0,
                    "normalization_version": NORMALIZATION_VERSION,
                    "not_run": "switched off by the operator",
                }
                continue
            if strict_offline and registry is REGISTRY:
                sources[source_id] = {
                    "source_id": source_id,
                    "status": "not-run",
                    "authoritative_url": source_spec(source_id).official_url,
                    "retrieval_query": query.model_dump(mode="json"),
                    "record_count": 0,
                    "normalization_version": NORMALIZATION_VERSION,
                    "not_run": "strict offline certification",
                }
                continue
            source = registry.get(source_id)
            submitted_at = time.monotonic()
            futures[executor.submit(_invoke, source_id, source, query)] = (
                source_id,
                submitted_at,
            )
        for future in as_completed(futures):
            source_id, submitted_at = futures[future]
            try:
                payload = future.result()
            except Exception as exc:  # noqa: BLE001 - fleet isolation
                payload = {"error": f"{type(exc).__name__}: {exc}"}
            sources[source_id] = _receipt(
                source_id,
                payload,
                query,
                elapsed_s=time.monotonic() - submitted_at,
            )

    ordered = {source_id: sources[source_id] for source_id in SOURCE_IDS}
    attempts = [
        {
            "source": source_id,
            "status": row["status"],
            "count": row.get("record_count", 0),
            "retrieved_at": row.get("retrieved_at"),
        }
        for source_id, row in ordered.items()
    ]
    statuses = {row["status"] for row in ordered.values()}
    family_status = (
        "complete" if statuses <= {"success"}
        else "failed" if statuses == {"failed"}
        else "partial"
    )
    envelope = make_provenance_envelope(
        "source_mesh",
        status=family_status,
        mode="parallel-scoped-source-mesh",
        retrieval_mode="live",
        retrieved_at=datetime.now(timezone.utc),
        record_count=sum(row.get("record_count", 0) for row in ordered.values()),
        attempts=attempts,
        limitations=(
            ["One or more child sources failed or were not applicable; "
             "successful child evidence remains usable."]
            if family_status != "complete" else []
        ),
    )
    required_origins = list(source_spec("source_mesh").coverage_origins)
    returned_origins = [
        source_id
        for source_id in required_origins
        if ordered[source_id]["status"] == "success"
    ]
    coverage_contract = {
        "required_origins": required_origins,
        "returned_origins": returned_origins,
        "incomplete_origins": [
            source_id
            for source_id in required_origins
            if source_id not in returned_origins
        ],
        "complete_within_boundary": (
            len(returned_origins) == len(required_origins)
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "client_name": client_name,
        "scope_designator": scope_designator,
        "query": query.model_dump(mode="json"),
        "sources": ordered,
        "coverage_contract": coverage_contract,
        "_provenance": envelope.model_dump(mode="json"),
    }


def child_attempt_rows(payload: Any) -> list[dict]:
    """Project stored child receipts into the sweep attempt manifest."""
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), dict):
        return []
    rows = []
    for source_id in SOURCE_IDS:
        receipt = payload["sources"].get(source_id) or {}
        status = receipt.get("status")
        row = {
            "source": source_id,
            "ok": status in {"success", "partial"},
            "elapsed_s": float(receipt.get("elapsed_s") or 0.0),
            "count": int(receipt.get("record_count") or 0),
            "source_receipt": {
                key: receipt.get(key)
                for key in (
                    "authoritative_url", "retrieval_url", "retrieved_at",
                    "immutable_receipt_sha256", "immutable_receipt_kind",
                    "normalization_version",
                )
                if receipt.get(key) is not None
            },
        }
        envelope = provenance_from_payload(
            receipt.get("payload"), source=source_id
        )
        if envelope is not None:
            row["provenance"] = envelope.public_projection()
        if status == "partial":
            row["partial"] = True
        if status not in {"success", "partial"}:
            row["error"] = (
                "not run" if status == "not-run" else "source retrieval failed")
        else:
            row["summary"] = f"{row['count']} scoped records returned"
        rows.append(row)
    return rows
