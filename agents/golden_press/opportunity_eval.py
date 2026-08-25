"""Pure, post-retrieval evaluation of an operator-approved opportunity gold set.

The caller supplies the gold contract and already-produced graph and product
mappings.  This module performs no file, database, clock, network, query, or
retrieval work.  Gold identities can measure an output; they cannot influence
how that output is discovered, classified, ranked, or rendered.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


EVALUATION_VERSION = "opportunity-gold-evaluation.v1"

_FITS = frozenset({"direct", "adjacent", "ambiguous", "unrelated"})
_ROUTES = frozenset({
    "direct", "unknown", "incumbent", "named_partner_teaming",
    "possible_subcontracting",
})
_ROUTE_ACTIONS = frozenset({"prime", "team", "verify"})
_STATES = frozenset({
    "qualified", "decision_required", "research_context", "excluded",
})
_SLOT_5_STATES = frozenset({
    "qualified", "decision_required", "research_context",
})
_SLOT_6_STATES = frozenset({
    "non_owning_route_reference", "explicit_research_gap",
})
_MODEL_AUTHORITY_WORDS = frozenset({
    "ai", "assistant", "claude", "codex", "language model", "llm", "model",
})
_ADJUDICATION_STATUSES = frozenset({
    "proposed_record_matrix_pending_operator_ratification",
    "ratified_operator_adjudication",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _key(value: Any) -> str:
    return _text(value).casefold()


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _identity(row: Mapping[str, Any]) -> str:
    return _text(
        row.get("record_id") or row.get("notice_id") or row.get("source_id")
        or row.get("id")
    )


def _aware_timestamp(value: Any) -> bool:
    text = _text(value)
    if "T" not in text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _duplicate_values(rows: list[Mapping[str, Any]], field: str) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for row in rows:
        value = _key(row.get(field))
        if not value:
            continue
        if value in seen:
            repeated.add(_text(row.get(field)))
        seen.add(value)
    return sorted(repeated)


def validate_gold_contract(contract: Mapping[str, Any]) -> list[str]:
    """Validate the authority boundary and the v1 contract shapes without I/O.

    The JSON Schema is the portable contract.  This small pure validator keeps
    the same high-value invariants executable without adding a schema-runtime
    dependency to the press.
    """
    errors: list[str] = []
    required = (
        "client", "contract_version", "classification_as_of",
        "boundary_authority", "adjudication_status", "note", "targets",
        "adjudications",
        "capability_evidence_anchors", "notice_family_expectations",
        "forecast_expectations",
    )
    for field in required:
        if field not in contract:
            errors.append(f"contract missing required field {field!r}")

    if not _text(contract.get("client")):
        errors.append("client must be non-empty")
    if not re.fullmatch(
            r"jtg-opportunity-gold\.v1\.\d{4}-\d{2}-\d{2}",
            _text(contract.get("contract_version"))):
        errors.append("contract_version must be a dated JTG opportunity-gold v1 value")
    if not _aware_timestamp(contract.get("classification_as_of")):
        errors.append("classification_as_of must be a timezone-aware timestamp")

    authority = _text(contract.get("boundary_authority"))
    authority_folded = authority.casefold()
    if not authority_folded.startswith("operator-approved "):
        errors.append("boundary_authority must state an operator-approved boundary")
    if any(re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])",
                     authority_folded)
           for word in _MODEL_AUTHORITY_WORDS):
        errors.append("boundary_authority cannot name a model as the authority")

    if contract.get("adjudication_status") not in _ADJUDICATION_STATUSES:
        errors.append("adjudication_status is unsupported")

    note = _text(contract.get("note")).casefold()
    for phrase in ("post-retrieval quality benchmark only",
                   "never enter retrieval queries"):
        if phrase not in note:
            errors.append(f"note must state {phrase!r}")

    targets = _rows(contract.get("targets"))
    adjudications = _rows(contract.get("adjudications"))
    anchors = _rows(contract.get("capability_evidence_anchors"))
    families = _rows(contract.get("notice_family_expectations"))
    forecasts = _rows(contract.get("forecast_expectations"))
    for name, collection in (
        ("targets", targets), ("adjudications", adjudications),
        ("capability_evidence_anchors", anchors),
        ("notice_family_expectations", families),
        ("forecast_expectations", forecasts),
    ):
        if not collection:
            errors.append(f"{name} must contain at least one row")

    for duplicate in _duplicate_values(targets, "id"):
        errors.append(f"duplicate target id {duplicate!r}")
    for duplicate in _duplicate_values(adjudications, "id"):
        errors.append(f"duplicate adjudication id {duplicate!r}")
    for duplicate in _duplicate_values(anchors, "id"):
        errors.append(f"duplicate capability anchor id {duplicate!r}")
    for duplicate in _duplicate_values(families, "solicitation_number"):
        errors.append(f"duplicate notice-family solicitation {duplicate!r}")
    for duplicate in _duplicate_values(forecasts, "id"):
        errors.append(f"duplicate forecast id {duplicate!r}")

    adjudicated_ids = {_key(row.get("id")) for row in adjudications}
    for index, row in enumerate(targets):
        prefix = f"targets[{index}]"
        if not _identity(row):
            errors.append(f"{prefix}.id must be non-empty")
        if row.get("kind") not in {"notice", "solicitation", "forecast", "award"}:
            errors.append(f"{prefix}.kind is unsupported")
        if not isinstance(row.get("must_lead"), bool):
            errors.append(f"{prefix}.must_lead must be boolean")
        if not _text(row.get("title_hint")) or not _text(row.get("why")):
            errors.append(f"{prefix} requires title_hint and why")
        if _key(row.get("id")) not in adjudicated_ids:
            errors.append(f"{prefix}.id has no adjudication")

    for index, row in enumerate(adjudications):
        prefix = f"adjudications[{index}]"
        if not _identity(row):
            errors.append(f"{prefix}.id must be non-empty")
        if row.get("expected_fit") not in _FITS:
            errors.append(f"{prefix}.expected_fit is unsupported")
        if row.get("expected_state") not in _STATES:
            errors.append(f"{prefix}.expected_state is unsupported")
        if "expected_route" in row and row.get("expected_route") not in _ROUTES:
            errors.append(f"{prefix}.expected_route is unsupported")
        if ("expected_route_action" in row
                and row.get("expected_route_action") not in _ROUTE_ACTIONS):
            errors.append(f"{prefix}.expected_route_action is unsupported")

    for index, row in enumerate(anchors):
        prefix = f"capability_evidence_anchors[{index}]"
        if not _identity(row) or not _text(row.get("supports")):
            errors.append(f"{prefix} requires id and supports")
        if row.get("expected_class") not in {
                "client_historical", "competitive_historical"}:
            errors.append(f"{prefix}.expected_class is unsupported")
        amount = row.get("published_obligations")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) \
                or amount < 0:
            errors.append(f"{prefix}.published_obligations must be non-negative")
        if not _text(row.get("figure_type")):
            errors.append(f"{prefix}.figure_type must be non-empty")

    for index, row in enumerate(families):
        prefix = f"notice_family_expectations[{index}]"
        if not _text(row.get("solicitation_number")):
            errors.append(f"{prefix}.solicitation_number must be non-empty")
        if not _text(row.get("expected_canonical_record_id")):
            errors.append(
                f"{prefix}.expected_canonical_record_id must be non-empty")
        member_ids = [
            _text(value) for value in row.get("expected_member_ids") or []
            if _text(value)
        ]
        if not member_ids or len({_key(value) for value in member_ids}) != len(
                member_ids):
            errors.append(
                f"{prefix}.expected_member_ids must be unique and non-empty")
        count = row.get("expected_member_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            errors.append(f"{prefix}.expected_member_count must be positive")
        elif member_ids and count != len(member_ids):
            errors.append(
                f"{prefix}.expected_member_count must equal expected_member_ids")
        if not _aware_timestamp(row.get("expected_deadline")):
            errors.append(f"{prefix}.expected_deadline must be timezone-aware")
        if row.get("expected_fit") not in _FITS:
            errors.append(f"{prefix}.expected_fit is unsupported")
        if row.get("expected_route") not in _ROUTES:
            errors.append(f"{prefix}.expected_route is unsupported")
        if row.get("expected_route_action") not in _ROUTE_ACTIONS:
            errors.append(f"{prefix}.expected_route_action is unsupported")
        if row.get("expected_slot_5_state") not in _SLOT_5_STATES:
            errors.append(f"{prefix}.expected_slot_5_state is unsupported")
        if row.get("expected_slot_6") not in _SLOT_6_STATES:
            errors.append(f"{prefix}.expected_slot_6 is unsupported")

    for index, row in enumerate(forecasts):
        prefix = f"forecast_expectations[{index}]"
        if not _identity(row):
            errors.append(f"{prefix}.id must be non-empty")
        if row.get("expected_slot") != "future-forecasts":
            errors.append(f"{prefix}.expected_slot must be future-forecasts")
        for field in ("predecessor_contract_id", "buyer_component", "buyer_office"):
            if not _text(row.get(field)):
                errors.append(f"{prefix}.{field} must be non-empty")
    return errors


def _index_graph_records(graph: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    indexed: dict[str, list[Mapping[str, Any]]] = {}
    for row in _rows(graph.get("records")):
        identity = _key(_identity(row))
        if identity:
            indexed.setdefault(identity, []).append(row)
    return indexed


def _id_set(value: Any) -> set[str]:
    identities: set[str] = set()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            identity = _identity(item) if isinstance(item, Mapping) else _text(item)
            if identity:
                identities.add(_key(identity))
    return identities


def _product_slots(product: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        _text(row.get("slot_id")): row
        for row in _rows(product.get("slots"))
        if _text(row.get("slot_id"))
    }


def _slot_rows(slot: Mapping[str, Any], field: str = "records") -> list[Mapping[str, Any]]:
    return _rows(slot.get(field))


def _row_matches_identity(row: Mapping[str, Any], identity: str) -> bool:
    return _key(_identity(row)) == _key(identity)


def _slot_5_state(slots: Mapping[str, Mapping[str, Any]], identity: str) -> str:
    slot = slots.get("federal-opportunities") or {}
    if any(_row_matches_identity(row, identity) for row in _slot_rows(slot)):
        return "qualified"
    if any(_row_matches_identity(row, identity)
           for row in _slot_rows(slot, "review_records")):
        return "decision_required"
    return "absent"


def _appears_in_product(slots: Mapping[str, Mapping[str, Any]], identity: str) -> bool:
    for slot in slots.values():
        for row in _slot_rows(slot) + _slot_rows(slot, "review_records"):
            if _row_matches_identity(row, identity):
                return True
    return False


def _outcome_state(
    identity: str,
    record: Mapping[str, Any],
    qualified_ids: set[str],
    held_ids: set[str],
    slots: Mapping[str, Mapping[str, Any]],
) -> str:
    if record.get("evidence_class") == "excluded" \
            or (record.get("projection_decision") or {}).get("disposition") \
            == "out_of_scope":
        return "excluded"
    slot_state = _slot_5_state(slots, identity)
    if _key(identity) in qualified_ids or slot_state == "qualified":
        return "qualified"
    decision = (record.get("projection_decision") or {}).get("disposition")
    if _key(identity) in held_ids or slot_state == "decision_required" \
            or decision == "needs_review":
        return "decision_required"
    return "research_context"


def _field(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    source_fields = row.get("source_fields") or {}
    if isinstance(source_fields, Mapping):
        for name in names:
            value = source_fields.get(name)
            if value not in (None, ""):
                return value
    return None


def _family_solicitations(family: Mapping[str, Any]) -> set[str]:
    values = {_key(family.get("solicitation_number"))}
    for row in _rows(family.get("family_lineage")):
        values.add(_key(row.get("solicitation_number")))
    return {value for value in values if value}


def _family_for_solicitation(
    graph: Mapping[str, Any], solicitation_number: str,
) -> Mapping[str, Any] | None:
    sought = _key(solicitation_number)
    families = _rows(graph.get("canonical_requirement_families"))
    for family in families:
        if sought in _family_solicitations(family):
            return family
    family_keys = {
        _text(row.get("requirement_family"))
        for row in _rows(graph.get("records"))
        if _key(_field(row, "solicitation_number", "sol_number")) == sought
        and _text(row.get("requirement_family"))
    }
    return next((row for row in families
                 if _text(row.get("requirement_family")) in family_keys), None)


def _canonical_family_record(
    family: Mapping[str, Any] | None,
    graph: Mapping[str, Any],
    indexed: Mapping[str, list[Mapping[str, Any]]],
    solicitation_number: str,
) -> Mapping[str, Any] | None:
    if family:
        canonical = _key(family.get("canonical_record_id"))
        if canonical and indexed.get(canonical):
            return indexed[canonical][0]
    sought = _key(solicitation_number)
    candidates = [
        row for row in _rows(graph.get("records"))
        if _key(_field(row, "solicitation_number", "sol_number")) == sought
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (
        row.get("evidence_class") == "current_opportunity",
        bool(_field(row, "response_deadline", "response_due")),
        _text(_field(row, "posted_date", "modified_date")),
        _identity(row),
    ))


def _family_member_ids(family: Mapping[str, Any] | None,
                       canonical: Mapping[str, Any] | None) -> set[str]:
    values: set[str] = set()
    for source in (family or {}, canonical or {}):
        for value in source.get("family_member_ids") or []:
            if _text(value):
                values.add(_key(value))
    if canonical and _identity(canonical):
        values.add(_key(_identity(canonical)))
    return values


def _row_references_family(
    row: Mapping[str, Any], solicitation_number: str, member_ids: set[str],
) -> bool:
    sought = _key(solicitation_number)
    if _key(_identity(row)) in member_ids:
        return True
    for field in (
        "solicitation_number", "opportunity_solicitation_number",
        "reference_solicitation_number",
    ):
        if _key(row.get(field)) == sought:
            return True
    for field in (
            "opportunity_source_id", "reference_source_id"):
        if _key(row.get(field)) in member_ids:
            return True
    graph_owners = {f"graph-record:{member}" for member in member_ids}
    if _key(row.get("notice_reference_key")) in graph_owners:
        return True
    return any(
        _key(receipt.get("source_id")) in member_ids
        for receipt in (
            _rows(row.get("evidence"))
            + _rows(row.get("evidence_receipts")))
    )


def _slot_5_family_state(
    slots: Mapping[str, Mapping[str, Any]], solicitation_number: str,
    member_ids: set[str],
) -> str:
    slot = slots.get("federal-opportunities") or {}
    for state, field in (("qualified", "records"),
                         ("decision_required", "review_records")):
        for row in _slot_rows(slot, field):
            if _key(_field(row, "solicitation_number", "sol_number")) \
                    == _key(solicitation_number) \
                    or _key(_identity(row)) in member_ids:
                return state
    return "research_context"


def _slot_6_family_state(
    slots: Mapping[str, Mapping[str, Any]], solicitation_number: str,
    member_ids: set[str],
) -> str:
    slot = slots.get("teaming-opportunities") or {}
    for row in _slot_rows(slot):
        if not _row_references_family(row, solicitation_number, member_ids):
            continue
        graph_owners = {f"graph-record:{member}" for member in member_ids}
        if (
            row.get("kind") == "teaming_route_reference"
            and row.get("non_owning_reference") is True
            and row.get("notice_reference_slot_id") ==
                "federal-opportunities"
            and _key(row.get("notice_reference_key")) in graph_owners
            and _key(_identity(row)) in member_ids
        ):
            return "non_owning_route_reference"
    gaps = slot.get("gaps") or []
    if any(_key(solicitation_number) in _key(gap) for gap in gaps):
        return "explicit_research_gap"
    return "missing"


def evaluate_opportunity_gold(
    contract: Mapping[str, Any], *, graph: Mapping[str, Any],
    product: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate already-produced graph/product mappings against exact gold rows."""
    contract_errors = validate_gold_contract(contract)
    checks: list[dict[str, Any]] = []

    def check(scope: str, identity: str, field: str,
              expected: Any, actual: Any) -> None:
        checks.append({
            "scope": scope,
            "identity": identity,
            "field": field,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
        })

    if contract_errors:
        return {
            "schema_version": EVALUATION_VERSION,
            "client": _text(contract.get("client")),
            "contract_version": _text(contract.get("contract_version")),
            "classification_as_of": _text(contract.get("classification_as_of")),
            "boundary_authority": _text(contract.get("boundary_authority")),
            "adjudication_status": _text(contract.get("adjudication_status")),
            "passed": False,
            "contract_errors": contract_errors,
            "checks": [],
            "mismatches": [],
        }

    indexed = _index_graph_records(graph)
    qualified_ids = _id_set(graph.get("qualified_opportunity_records"))
    qualified_ids |= _id_set(graph.get("qualified_opportunities"))
    held_ids = _id_set(graph.get("held_opportunities"))
    slots = _product_slots(product)
    expected_clock = _text(contract.get("classification_as_of"))
    graph_clock = _text(
        (graph.get("classification_context") or {}).get("as_of"))
    product_receipt = product.get("source_receipt") or {}
    if not isinstance(product_receipt, Mapping):
        product_receipt = {}
    check("clock", _text(contract.get("client")),
          "graph_classification_as_of", expected_clock, graph_clock)
    check("clock", _text(contract.get("client")),
          "product_classification_as_of", expected_clock,
          _text(product_receipt.get("classification_as_of")))

    for target in _rows(contract.get("targets")):
        identity = _text(target.get("id"))
        rows = indexed.get(_key(identity), [])
        check("target", identity, "canonical_graph_identity", 1, len(rows))

    for expected in _rows(contract.get("adjudications")):
        identity = _text(expected.get("id"))
        rows = indexed.get(_key(identity), [])
        check("adjudication", identity, "canonical_graph_identity", 1, len(rows))
        if not rows:
            continue
        record = rows[0]
        check("adjudication", identity, "service_fit",
              expected.get("expected_fit"), record.get("service_fit"))
        if "expected_route" in expected:
            check("adjudication", identity, "commercial_route",
                  expected.get("expected_route"), record.get("commercial_route"))
        if "expected_route_action" in expected:
            check("adjudication", identity, "route_action",
                  expected.get("expected_route_action"),
                  record.get("route_action"))
        state = _outcome_state(
            identity, record, qualified_ids, held_ids, slots)
        check("adjudication", identity, "outcome_state",
              expected.get("expected_state"), state)
        expected_state = expected.get("expected_state")
        if expected_state == "qualified":
            check("product", identity, "slot_5_state", "qualified",
                  _slot_5_state(slots, identity))
        elif expected_state == "decision_required":
            check("product", identity, "slot_5_state", "decision_required",
                  _slot_5_state(slots, identity))
        elif expected_state == "excluded":
            check("product", identity, "externally_absent", False,
                  _appears_in_product(slots, identity))

    for expected in _rows(contract.get("capability_evidence_anchors")):
        identity = _text(expected.get("id"))
        rows = indexed.get(_key(identity), [])
        check("capability_anchor", identity, "canonical_graph_identity", 1, len(rows))
        if not rows:
            continue
        record = rows[0]
        check("capability_anchor", identity, "evidence_class",
              expected.get("expected_class"), record.get("evidence_class"))
        combined = " ".join(_text(value) for value in (
            record.get("title"), record.get("description"),
            record.get("matched_sentence"), record.get("fit_basis"),
            " ".join(record.get("relevance_matched") or []),
        )).casefold()
        supports = _text(expected.get("supports")).casefold()
        check("capability_anchor", identity, "supports", True,
              supports in combined)
        check("capability_anchor", identity, "published_obligations",
              expected.get("published_obligations"),
              _field(record, "published_obligations", "obligated_dollars"))
        check("capability_anchor", identity, "figure_type",
              expected.get("figure_type"), record.get("figure_type"))

    for expected in _rows(contract.get("notice_family_expectations")):
        solicitation = _text(expected.get("solicitation_number"))
        family = _family_for_solicitation(graph, solicitation)
        check("notice_family", solicitation, "family_present", True,
              family is not None)
        canonical = _canonical_family_record(
            family, graph, indexed, solicitation)
        if canonical is None:
            continue
        member_ids = _family_member_ids(family, canonical)
        check("notice_family", solicitation, "canonical_record_id",
              expected.get("expected_canonical_record_id"),
              _text((family or {}).get("canonical_record_id")
                    or _identity(canonical)))
        check("notice_family", solicitation, "family_member_ids",
              sorted(_text(value) for value in
                     expected.get("expected_member_ids") or []),
              sorted(member_ids))
        actual_count = (
            family.get("family_member_count") if family else None
        ) or len(member_ids)
        check("notice_family", solicitation, "family_member_count",
              expected.get("expected_member_count"), actual_count)
        check("notice_family", solicitation, "response_deadline",
              expected.get("expected_deadline"),
              _field(canonical, "response_deadline", "response_due"))
        check("notice_family", solicitation, "service_fit",
              expected.get("expected_fit"), canonical.get("service_fit"))
        check("notice_family", solicitation, "commercial_route",
              expected.get("expected_route"), canonical.get("commercial_route"))
        check("notice_family", solicitation, "route_action",
              expected.get("expected_route_action"),
              canonical.get("route_action"))
        check("notice_family", solicitation, "slot_5_state",
              expected.get("expected_slot_5_state"),
              _slot_5_family_state(slots, solicitation, member_ids))
        check("notice_family", solicitation, "slot_6_state",
              expected.get("expected_slot_6"),
              _slot_6_family_state(slots, solicitation, member_ids))

    for expected in _rows(contract.get("forecast_expectations")):
        identity = _text(expected.get("id"))
        rows = indexed.get(_key(identity), [])
        check("forecast", identity, "canonical_graph_identity", 1, len(rows))
        if not rows:
            continue
        record = rows[0]
        expected_slot = _text(expected.get("expected_slot"))
        slot = slots.get(expected_slot) or {}
        actual_slot = expected_slot if any(
            _row_matches_identity(row, identity) for row in _slot_rows(slot)
        ) else "absent"
        check("forecast", identity, "owning_slot", expected_slot, actual_slot)
        check("forecast", identity, "predecessor_contract_id",
              expected.get("predecessor_contract_id"),
              _field(record, "predecessor_contract_id", "predecessor_award_id"))
        buyer = record.get("buyer") or {}
        if not isinstance(buyer, Mapping):
            buyer = {}
        check("forecast", identity, "buyer_component",
              expected.get("buyer_component"),
              _field(record, "sub_agency") or buyer.get("sub_agency"))
        check("forecast", identity, "buyer_office",
              expected.get("buyer_office"),
              _field(record, "office") or buyer.get("office"))

    mismatches = [row for row in checks if not row["passed"]]
    evaluated = sorted({row["identity"] for row in checks})
    return {
        "schema_version": EVALUATION_VERSION,
        "client": _text(contract.get("client")),
        "contract_version": _text(contract.get("contract_version")),
        "classification_as_of": _text(contract.get("classification_as_of")),
        "boundary_authority": _text(contract.get("boundary_authority")),
        "adjudication_status": _text(contract.get("adjudication_status")),
        "passed": not mismatches,
        "contract_errors": [],
        "evaluated_identities": evaluated,
        "checks": checks,
        "mismatches": mismatches,
    }


__all__ = (
    "EVALUATION_VERSION",
    "evaluate_opportunity_gold",
    "validate_gold_contract",
)
