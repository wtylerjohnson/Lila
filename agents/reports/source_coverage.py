"""Public, deterministic source-coverage projection for Signal Boards.

The stored sweep's ``results._attempts`` manifest is the sole authority for
whether a research lane ran in this refresh.  Preserved result payloads do not
count as a fresh attempt. Public rows intentionally retain only the lane
identity and a coarse status/provenance mode; vendor error text never crosses
this boundary.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tools.api.provenance import (
    ProvenanceEnvelope,
    provenance_from_payload,
)
from tools.api.source_catalog import (
    CONNECTED_NOT_USED,
    DEFAULT_CHILD_SOURCES_BY_PARENT,
    SourceSpec,
    STANDARD_SOURCE_SPECS,
    STANDARD_SWEEP_LANES,
)

RETURNED = "RETURNED"
RETURNED_FALLBACK = "RETURNED VIA OFFICIAL FALLBACK"
PARTIAL = "PARTIAL RETURN"
PARTIAL_FALLBACK = "PARTIAL VIA OFFICIAL FALLBACK"
CURRENT_SNAPSHOT = "CURRENT-DAY OFFICIAL SNAPSHOT"
CURRENT_FALLBACK_SNAPSHOT = "CURRENT-DAY OFFICIAL FALLBACK SNAPSHOT"
STALE_SNAPSHOT = "STALE OFFICIAL SNAPSHOT"
FAILED = "RATE-LIMITED/FAILED"
NOT_RUN = "NOT RUN THIS REFRESH"
SCOPE_EXCLUDED = "EXCLUDED BY ENGAGEMENT SCOPE"
NOT_USED = "NOT USED THIS REFRESH"

PARTIAL_STATUSES = frozenset({PARTIAL, PARTIAL_FALLBACK})
FALLBACK_STATUSES = frozenset({
    RETURNED_FALLBACK, PARTIAL_FALLBACK, CURRENT_FALLBACK_SNAPSHOT,
})
CURRENT_SNAPSHOT_STATUSES = frozenset({
    CURRENT_SNAPSHOT, CURRENT_FALLBACK_SNAPSHOT,
})
RETURNED_STATUSES = frozenset({
    RETURNED, RETURNED_FALLBACK, PARTIAL, PARTIAL_FALLBACK,
    CURRENT_SNAPSHOT, CURRENT_FALLBACK_SNAPSHOT,
})
PUBLIC_LANE_STATUSES = frozenset({
    *RETURNED_STATUSES, STALE_SNAPSHOT, FAILED, NOT_RUN, SCOPE_EXCLUDED,
})

# Sweep-level coverage verdict vocabulary.  These values are intentionally
# separate from the public presentation badges above: the badges explain how
# one lane returned, while the verdict answers whether every required lane was
# screened completely in this sweep.
VERDICT_COMPLETE = "complete"
VERDICT_SCREENED_ZERO = "screened-zero"
VERDICT_PARTIAL = "partial"
VERDICT_STALE = "stale"
VERDICT_FAILED = "failed"
VERDICT_NOT_RUN = "not-run"
VERDICT_SCOPE_EXCLUDED = "scope-excluded"
VERDICT_GATED = "gated"
VERDICT_COMPLETE_WITHIN_BOUNDARY = "complete-within-boundary"

COMPREHENSIVE = "comprehensive"
NOT_COMPREHENSIVE = "not-comprehensive"

COMPLETE_VERDICT_STATES = frozenset({
    VERDICT_COMPLETE, VERDICT_SCREENED_ZERO,
    VERDICT_COMPLETE_WITHIN_BOUNDARY,
})
BLOCKING_VERDICT_STATES = frozenset({
    VERDICT_PARTIAL, VERDICT_STALE, VERDICT_FAILED,
    VERDICT_NOT_RUN, VERDICT_GATED,
})


@dataclass(frozen=True)
class LaneCoverageVerdict:
    """One standard lane's sweep-completeness classification.

    ``dimensions`` retains orthogonal defects such as a partial response that
    also came from a stale snapshot.  ``state`` is the stable primary label;
    callers deciding completeness must inspect all dimensions.
    """

    source: str
    state: str
    required: bool
    dimensions: tuple[str, ...]
    coverage_class: str
    coverage_boundary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "state": self.state,
            "required": self.required,
            "dimensions": list(self.dimensions),
            "coverage_class": self.coverage_class,
            "coverage_boundary": self.coverage_boundary,
        }


@dataclass(frozen=True)
class SweepCoverageVerdict:
    """Truthful aggregate verdict for the required standard sweep lanes."""

    verdict: str
    comprehensive: bool
    lanes: tuple[LaneCoverageVerdict, ...]
    blocking_sources: tuple[str, ...]
    advisory_gaps: tuple[str, ...] = ()
    bounded_sources: tuple[str, ...] = ()
    universe_comprehensive: bool = False

    def as_dict(self) -> dict[str, Any]:
        counts = {
            state: sum(state in lane.dimensions for lane in self.lanes)
            for state in (
                VERDICT_COMPLETE, VERDICT_SCREENED_ZERO, VERDICT_PARTIAL,
                VERDICT_STALE, VERDICT_FAILED, VERDICT_NOT_RUN,
                VERDICT_SCOPE_EXCLUDED, VERDICT_GATED,
                VERDICT_COMPLETE_WITHIN_BOUNDARY,
            )
        }
        return {
            "verdict": self.verdict,
            "comprehensive": self.comprehensive,
            "required_lanes": sum(lane.required for lane in self.lanes),
            "blocking_sources": list(self.blocking_sources),
            "coverage_contract_met": self.comprehensive,
            "source_coverage_complete": self.comprehensive,
            "universe_comprehensive": self.universe_comprehensive,
            "advisory_gaps": list(self.advisory_gaps),
            "bounded_sources": list(self.bounded_sources),
            "counts": counts,
            "lanes": [lane.as_dict() for lane in self.lanes],
        }


def _source_name(value: Any) -> str:
    return " ".join(str(value or "").split())


def _unknown_label(source: str) -> str:
    words = source.replace("_", " ").replace(".", " ").split()
    return " ".join(word.upper() if len(word) <= 3 else word.title()
                    for word in words) or "Unidentified attempted lane"


def _collapsed_status(outcomes: list[str] | None) -> str:
    if outcomes is None:
        return NOT_RUN
    if FAILED in outcomes:
        return FAILED
    if PARTIAL in outcomes:
        return PARTIAL
    if RETURNED in outcomes:
        return RETURNED
    return SCOPE_EXCLUDED


def counts_from_lanes(rows: list[dict]) -> dict:
    """Reconcile public counts from final lane statuses, never caller math."""
    return {
        "standard": sum(row.get("scope") == "standard" for row in rows),
        "additional": sum(row.get("scope") == "additional" for row in rows),
        "attempted": sum(
            row.get("status") not in {NOT_RUN, SCOPE_EXCLUDED}
            for row in rows),
        "returned": sum(row.get("status") in RETURNED_STATUSES for row in rows),
        "fallback": sum(
            row.get("fallback") is True
            or row.get("status") in FALLBACK_STATUSES
            for row in rows),
        "partial": sum(
            row.get("partial") is True
            or row.get("status") in PARTIAL_STATUSES
            for row in rows),
        "current_snapshot": sum(
            row.get("current_snapshot") is True
            or row.get("status") in CURRENT_SNAPSHOT_STATUSES
            for row in rows),
        "stale": sum(
            row.get("stale") is True
            or row.get("status") == STALE_SNAPSHOT
            for row in rows),
        "failed": sum(row.get("status") == FAILED for row in rows),
        "not_run": sum(row.get("status") == NOT_RUN for row in rows),
        "scope_excluded": sum(
            row.get("status") == SCOPE_EXCLUDED for row in rows),
        "connected_not_used": len(CONNECTED_NOT_USED),
    }


def coverage_from_attempts(attempts: Any) -> dict:
    """Project one public source-coverage model from attempt rows.

    Unknown future lane names are appended in stable lexical order.  Duplicate
    rows collapse conservatively: any failed occurrence makes that lane failed
    rather than allowing a later/earlier success row to hide the failure.
    """
    verdicts: dict[str, list[str]] = {}
    unidentified = 0
    for row in attempts if isinstance(attempts, list) else []:
        if not isinstance(row, dict):
            unidentified += 1
            continue
        source = _source_name(row.get("source"))
        if not source:
            unidentified += 1
            continue
        provenance = row.get("provenance")
        scope_excluded = bool(
            row.get("scope_excluded") is True
            or (isinstance(provenance, dict)
                and provenance.get("mode") == "scope-excluded")
        )
        outcome = (
            FAILED if row.get("ok") is not True
            else PARTIAL if row.get("partial") is True
            else SCOPE_EXCLUDED if scope_excluded
            else RETURNED
        )
        verdicts.setdefault(source, []).append(outcome)

    canonical_keys = {source for source, _label in STANDARD_SWEEP_LANES}
    rows: list[dict] = []
    for source, label in STANDARD_SWEEP_LANES:
        spec = next(item for item in STANDARD_SOURCE_SPECS
                    if item.task_key == source)
        status = _collapsed_status(verdicts.get(source))
        row = {
            "source": source,
            "label": label,
            "status": status,
            "scope": "standard",
            "coverage_class": spec.coverage_class,
            "coverage_boundary": spec.coverage_boundary,
        }
        children = DEFAULT_CHILD_SOURCES_BY_PARENT.get(source, ())
        if children:
            child_labels = ", ".join(child.label for child in children)
            row["detail"] = f"Configured source: {child_labels}"
        rows.append(row)

    unknown_sources = sorted(
        (source for source in verdicts if source not in canonical_keys),
        key=lambda value: (value.casefold(), value),
    )
    for source in unknown_sources:
        rows.append({
            "source": source,
            "label": _unknown_label(source),
            "status": _collapsed_status(verdicts[source]),
            "scope": "additional",
        })
    for index in range(1, unidentified + 1):
        rows.append({
            "source": f"unidentified_attempt_{index}",
            "label": f"Unidentified attempted lane {index}",
            "status": FAILED,
            "scope": "additional",
        })

    counts = counts_from_lanes(rows)
    connected = [
        {
            "source": source,
            "label": label,
            "detail": detail,
            "status": NOT_USED,
        }
        for source, label, detail in CONNECTED_NOT_USED
    ]
    return {"lanes": rows, "connected_not_used": connected,
            "counts": counts}


def coverage_from_sweep(sweep: Any) -> dict:
    """Project attempts plus public-safe result provenance modes.

    A top-level task can complete successfully by returning an explicitly
    sourced fallback or stale official snapshot.  Those modes must not be
    flattened to ``RETURNED`` merely because the runner caught no exception.
    """
    results = sweep.get("results") if isinstance(sweep, dict) else None
    attempts = results.get("_attempts") if isinstance(results, dict) else None
    coverage = coverage_from_attempts(attempts)
    if not isinstance(results, dict):
        return coverage

    by_source = {row["source"]: row for row in coverage["lanes"]}
    for spec in STANDARD_SOURCE_SPECS:
        if not spec.task_key or not spec.result_key:
            continue
        row = by_source.get(spec.task_key)
        if not isinstance(row, dict) or row.get("status") not in {
                RETURNED, PARTIAL, SCOPE_EXCLUDED}:
            continue
        envelope = provenance_from_payload(
            results.get(spec.result_key), source=spec.identity)
        if envelope is None:
            continue
        _apply_provenance(row, envelope)

    rows = coverage["lanes"]
    coverage["counts"] = counts_from_lanes(rows)
    return coverage


def _apply_provenance(row: dict, envelope: ProvenanceEnvelope) -> None:
    """Refine a returned lane from the generic, public-safe envelope."""
    if envelope.mode == "scope-excluded":
        status = SCOPE_EXCLUDED
    elif envelope.status == "failed":
        status = FAILED
    elif envelope.status == "partial":
        status = PARTIAL_FALLBACK if envelope.fallback else PARTIAL
    elif envelope.stale:
        status = STALE_SNAPSHOT
    elif envelope.retrieval_mode == "official-cache":
        status = (CURRENT_FALLBACK_SNAPSHOT if envelope.fallback
                  else CURRENT_SNAPSHOT)
    elif envelope.fallback:
        status = RETURNED_FALLBACK
    else:
        status = RETURNED
    row["status"] = status
    # Cache age is orthogonal to completeness. Keep a true-only dimension only
    # when PARTIAL is the primary badge; the other statuses already encode it.
    if envelope.status == "partial" and envelope.stale:
        row["stale"] = True
    elif (envelope.status == "partial"
          and envelope.retrieval_mode == "official-cache"):
        row["current_snapshot"] = True
    if envelope.public_detail:
        row["detail"] = envelope.public_detail


def _explicit_lane_count(sweep: Any, source: str) -> int | None:
    """Return a trustworthy explicit row count, if the sweep carried one.

    Zero is meaningful only when the collector or runner recorded it.  Absence
    of a count never becomes zero by inference.
    """
    results = sweep.get("results") if isinstance(sweep, Mapping) else None
    if not isinstance(results, Mapping):
        return None

    spec = next(
        (item for item in STANDARD_SOURCE_SPECS
         if item.task_key == source),
        None,
    )
    if spec is not None and spec.result_key:
        payload = results.get(spec.result_key)
        envelope = provenance_from_payload(payload, source=spec.identity)
        if envelope is not None and envelope.record_count is not None:
            return envelope.record_count
    else:
        payload = None

    attempt_counts = []
    attempts = results.get("_attempts")
    for row in attempts if isinstance(attempts, list) else []:
        if (not isinstance(row, Mapping)
                or _source_name(row.get("source")) != source):
            continue
        count = row.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            attempt_counts.append(count)
    if attempt_counts:
        # Duplicate successful rows collapse conservatively.  One positive
        # return proves this was not a screened-zero lane.
        return max(attempt_counts)

    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, Mapping):
        for key in ("items", "rows", "records", "buyers", "notices", "matched"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def _lane_verdict(
    row: Mapping[str, Any],
    *,
    spec: SourceSpec,
    explicit_count: int | None,
    gated: bool,
    scope_excluded: bool,
    bounded_contract_complete: bool,
) -> LaneCoverageVerdict:
    source = _source_name(row.get("source"))
    status = row.get("status")

    if status == SCOPE_EXCLUDED or scope_excluded:
        dimensions = (VERDICT_SCOPE_EXCLUDED,)
        required = False
    elif gated:
        dimensions = (VERDICT_GATED,)
        required = spec.coverage_class != "advisory"
    elif status == FAILED:
        dimensions = (VERDICT_FAILED,)
        required = spec.coverage_class != "advisory"
    elif status == NOT_RUN:
        dimensions = (VERDICT_NOT_RUN,)
        required = spec.coverage_class != "advisory"
    else:
        defects = []
        if status in PARTIAL_STATUSES or row.get("partial") is True:
            defects.append(VERDICT_PARTIAL)
        if status == STALE_SNAPSHOT or row.get("stale") is True:
            defects.append(VERDICT_STALE)
        if (defects == [VERDICT_PARTIAL]
                and spec.coverage_class == "required-bounded"
                and bounded_contract_complete):
            dimensions = (VERDICT_COMPLETE_WITHIN_BOUNDARY,)
        elif defects:
            dimensions = tuple(defects)
        elif status in RETURNED_STATUSES:
            if spec.coverage_class == "required-bounded":
                dimensions = (
                    (VERDICT_COMPLETE_WITHIN_BOUNDARY,
                     VERDICT_SCREENED_ZERO)
                    if bounded_contract_complete and explicit_count == 0
                    else (VERDICT_COMPLETE_WITHIN_BOUNDARY,)
                    if bounded_contract_complete
                    else (VERDICT_PARTIAL,)
                )
            else:
                dimensions = (
                    VERDICT_SCREENED_ZERO if explicit_count == 0
                    else VERDICT_COMPLETE,
                )
        else:
            # Unknown future status vocabulary fails closed.  It cannot mint a
            # comprehensive claim simply because the presentation layer has
            # not learned the new badge yet.
            dimensions = (VERDICT_FAILED,)
        required = spec.coverage_class != "advisory"

    return LaneCoverageVerdict(
        source=source,
        state=dimensions[0],
        required=required,
        dimensions=dimensions,
        coverage_class=spec.coverage_class,
        coverage_boundary=spec.coverage_boundary,
    )


def _bounded_contract_complete(sweep: Any, spec: SourceSpec) -> bool:
    """True only when every origin inside a declared bounded lane returned.

    ``partial`` can mean either an interrupted pull or a deliberately narrower
    official surface.  A bounded lane passes only in the second case, when the
    source declares its boundary and every child origin in that boundary has a
    successful provenance attempt.
    """
    if spec.coverage_class != "required-bounded" or not spec.result_key:
        return False
    results = sweep.get("results") if isinstance(sweep, Mapping) else None
    if not isinstance(results, Mapping):
        return False
    envelope = provenance_from_payload(
        results.get(spec.result_key), source=spec.identity)
    if (envelope is not None and not envelope.stale and envelope.attempts
            and spec.coverage_origins):
        attempted = {attempt.source for attempt in envelope.attempts}
        returned = {
            attempt.source
            for attempt in envelope.attempts
            if attempt.status == "success"
        }
        required_origins = set(spec.coverage_origins)
        if required_origins.intersection(attempted) or len(required_origins) > 1:
            return required_origins.issubset(returned)

    attempts = results.get("_attempts")
    for row in attempts if isinstance(attempts, list) else []:
        if (not isinstance(row, Mapping)
                or _source_name(row.get("source")) != spec.task_key):
            continue
        contract = row.get("coverage_contract")
        if not isinstance(contract, Mapping):
            continue
        required = {
            _source_name(origin)
            for origin in contract.get("required_origins", ())
        }
        returned = {
            _source_name(origin)
            for origin in contract.get("returned_origins", ())
        }
        return bool(
            contract.get("complete_within_boundary") is True
            and required == set(spec.coverage_origins)
            and required.issubset(returned)
        )
    return False


def coverage_verdict_from_sweep(
    sweep: Any,
    *,
    gated_sources: Iterable[str] = (),
    scope_excluded_sources: Iterable[str] = (),
) -> SweepCoverageVerdict:
    """Classify whether the required standard lanes form a complete sweep.

    A complete zero-result screen is positive coverage evidence, not a failure.
    Scope-excluded lanes are named but are outside the required denominator.
    Partial, stale, failed, unrun, or explicitly gated required lanes always
    make the aggregate verdict ``not-comprehensive``.
    """
    gated = {_source_name(source) for source in gated_sources}
    scope_excluded = {
        _source_name(source) for source in scope_excluded_sources
    }
    canonical = {source for source, _label in STANDARD_SWEEP_LANES}
    unknown = sorted((gated | scope_excluded) - canonical)
    if unknown:
        raise ValueError(
            "coverage overrides contain unknown standard lanes: "
            + ", ".join(unknown)
        )
    overlap = sorted(gated & scope_excluded)
    if overlap:
        raise ValueError(
            "a lane cannot be both gated and scope-excluded: "
            + ", ".join(overlap)
        )

    coverage = coverage_from_sweep(sweep)
    rows = [
        row for row in coverage.get("lanes", [])
        if isinstance(row, Mapping) and row.get("scope") == "standard"
    ]
    spec_by_task = {
        spec.task_key: spec for spec in STANDARD_SOURCE_SPECS
        if spec.task_key
    }
    lanes = tuple(
        _lane_verdict(
            row,
            spec=spec_by_task[row["source"]],
            explicit_count=_explicit_lane_count(sweep, row["source"]),
            gated=row["source"] in gated,
            scope_excluded=row["source"] in scope_excluded,
            bounded_contract_complete=_bounded_contract_complete(
                sweep, spec_by_task[row["source"]]),
        )
        for row in rows
    )
    blocking_sources = tuple(
        lane.source for lane in lanes
        if lane.required and any(
            dimension in BLOCKING_VERDICT_STATES
            for dimension in lane.dimensions
        )
    )
    comprehensive = (
        len(lanes) == len(STANDARD_SWEEP_LANES)
        and not blocking_sources
        and all(
            (not lane.required)
            or set(lane.dimensions).issubset(COMPLETE_VERDICT_STATES)
            for lane in lanes
        )
    )
    advisory_gaps = tuple(
        lane.source for lane in lanes
        if lane.coverage_class == "advisory" and any(
            dimension in BLOCKING_VERDICT_STATES
            for dimension in lane.dimensions)
    )
    bounded_sources = tuple(
        lane.source for lane in lanes
        if lane.coverage_class == "required-bounded"
    )
    universe_comprehensive = bool(
        comprehensive
        and not advisory_gaps
        and not bounded_sources
        and all(lane.coverage_class == "required-exhaustive"
                for lane in lanes if lane.required)
    )
    return SweepCoverageVerdict(
        verdict=COMPREHENSIVE if comprehensive else NOT_COMPREHENSIVE,
        comprehensive=comprehensive,
        lanes=lanes,
        blocking_sources=blocking_sources,
        advisory_gaps=advisory_gaps,
        bounded_sources=bounded_sources,
        universe_comprehensive=universe_comprehensive,
    )
