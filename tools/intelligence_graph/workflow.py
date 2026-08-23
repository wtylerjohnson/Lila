"""Operating contract for intelligence-graph builds and reviews.

The workflow contract keeps model responsibilities complementary.  One role
owns production mutations.  Other roles inspect receipts, rendered artifacts,
or classifier-generated ambiguity without rewriting the same schema or
renderer independently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


WORKFLOW_CONTRACT_VERSION = "intelligence-graph-workflow-v1"

ROLE_CONTRACT: dict[str, dict[str, Any]] = {
    "builder": {
        "mode": "write",
        "owns": [
            "schema",
            "store",
            "adapters",
            "classifiers",
            "tests",
            "renderer",
        ],
        "produces": [
            "classified_pack",
            "moved_record_receipt",
            "aggregate_receipt",
            "test_receipt",
            "rendered_review_copy",
        ],
    },
    "reviewer": {
        "mode": "read_only",
        "inspects": [
            "fixtures",
            "moved_records",
            "aggregate_changes",
            "invariants",
        ],
        "produces": ["review_receipt"],
    },
    "visual_qa": {
        "mode": "read_only",
        "starts_after": "data_layer_passed",
        "inspects": ["rendered_desktop", "rendered_print"],
        "produces": ["visual_qa_receipt"],
    },
    "research_agent": {
        "mode": "queue_only",
        "reads": ["research_queue"],
        "produces": ["ambiguity_resolution"],
    },
}

EXCLUSIVE_WRITE_DOMAINS = {
    "schema": "builder",
    "store": "builder",
    "adapters": "builder",
    "classifiers": "builder",
    "tests": "builder",
    "renderer": "builder",
}


def validate_role_contract(
        roles: dict[str, dict[str, Any]] | None = None) -> list[dict]:
    """Return violations when a production domain has multiple writers."""
    contract = roles or ROLE_CONTRACT
    violations: list[dict] = []
    for domain, expected_owner in sorted(EXCLUSIVE_WRITE_DOMAINS.items()):
        writers = sorted(
            role for role, definition in contract.items()
            if domain in (definition.get("owns") or [])
        )
        if writers != [expected_owner]:
            violations.append({
                "rule_id": "W001_EXCLUSIVE_WRITE_OWNER",
                "domain": domain,
                "expected_owner": expected_owner,
                "writers": writers,
            })
    for role, definition in sorted(contract.items()):
        if definition.get("mode") != "write" and definition.get("owns"):
            violations.append({
                "rule_id": "W002_READ_ROLE_CANNOT_OWN_PRODUCTION_DOMAIN",
                "role": role,
                "domains": sorted(definition.get("owns") or []),
            })
    return violations


def workflow_contract_receipt() -> dict:
    violations = validate_role_contract()
    return {
        "schema_version": WORKFLOW_CONTRACT_VERSION,
        "roles": ROLE_CONTRACT,
        "exclusive_write_domains": EXCLUSIVE_WRITE_DOMAINS,
        "certified": not bool(violations),
        "violations": violations,
    }


def _queue_id(record_id: Any, dimensions: Iterable[str]) -> str:
    basis = json.dumps({
        "record_id": str(record_id or ""),
        "dimensions": sorted(set(dimensions)),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def build_ambiguity_queue(records: list[dict]) -> list[dict]:
    """Queue only ambiguity explicitly emitted by the classifier.

    Excluded noise, unrelated scope, and stated-past records are terminal
    classifier outcomes.  They do not create open-ended human research work.
    Route eligibility is queued only when the classifier has explicitly held a
    current opportunity for an eligible access path.
    """
    queued: list[dict] = []
    for row in records:
        if (row.get("evidence_class") == "excluded" or
                row.get("service_fit") == "unrelated" or
                row.get("window_state") == "stated_past"):
            continue
        dimensions: list[str] = []
        reasons: list[str] = []
        if row.get("evidence_class") == "ambiguous":
            dimensions.append("evidence_class")
            reasons.append(str(row.get("evidence_basis") or
                               "evidence class is ambiguous"))
        if row.get("service_fit") == "ambiguous":
            dimensions.append("service_fit")
            reasons.append(str(row.get("service_fit_basis") or
                               "service fit is ambiguous"))
        if row.get("qualification_state") == "needs_eligible_route":
            dimensions.append("route_eligibility")
            reasons.append(str(row.get("qualification_reason") or
                               "eligible commercial route is unresolved"))
        if not dimensions:
            continue
        record_id = row.get("record_id")
        queued.append({
            "queue_id": _queue_id(record_id, dimensions),
            "record_id": record_id,
            "requirement_family": row.get("requirement_family"),
            "title": row.get("title"),
            "source_url": row.get("url") or row.get("source_url"),
            "ambiguous_dimensions": sorted(set(dimensions)),
            "reasons": list(dict.fromkeys(reasons)),
            "evidence_class": row.get("evidence_class"),
            "service_fit": row.get("service_fit"),
            "commercial_route": row.get("commercial_route"),
            "window_state": row.get("window_state"),
            "relationship_provenance": row.get(
                "relationship_provenance") or {},
            "requested_resolution": (
                "Resolve only the listed dimensions from sourced evidence; "
                "return the source, exact evidence span, and ruling."),
        })
    return sorted(queued, key=lambda row: (
        str(row.get("record_id") or ""), row["queue_id"]))
