"""Strict Live SAM projection for report consumers.

The immutable Assess run remains the intelligence boundary. This module is
the deterministic compatibility adapter between that boundary and established
report models: it validates an exact current pointer against the report's
sweep, scope, profile, and mutable projection-input manifest, then re-joins
ledger families to their raw SAM presentation fields.

No current pointer means the client has not been cut over and callers may use
the documented legacy path. A present but invalid or stale pointer is reported
as ``invalid`` so the fallback is visible rather than silently consuming old
ledger truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from agents.assess.binding import digest
from agents.assess.contracts import (
    AssessRun,
    CoverageStatus,
    LiveClassification,
    LiveRecommendation,
    LiveSolicitation,
    LiveSolicitationLedger,
    SourceLane,
)
from agents.assess.ledger import (
    AssessLedgerError,
    _posting_order,
    _scope_designator,
    adapt_live_ledger,
    assess_projection_input_manifest,
    build_source_coverage,
    current_assess_pointer_path,
    load_current_assess_run,
    scope_from_sweep,
)
from agents.assess.reviewed_cases import ReviewedCase, ReviewedCases, load_cases


class LiveReportState(str, Enum):
    ABSENT = "absent"
    CURRENT = "current"
    INVALID = "invalid"


def utc_today() -> date:
    """Conservative shared cutoff for date-only federal response deadlines."""
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class LiveReportNotice:
    """One ledger family plus exact raw postings used for presentation only."""

    record: LiveSolicitation
    current: dict[str, Any]
    postings: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class LiveReportProjection:
    """Validated report input and its explicit cutover state."""

    state: LiveReportState
    client_name: str
    scope_designator: str
    run_id: Optional[str] = None
    notices: tuple[LiveReportNotice, ...] = ()
    verdict_totals: tuple[tuple[str, int], ...] = ()
    can_release: Optional[bool] = None
    live_coverage_complete: Optional[bool] = None
    problem: Optional[str] = None
    # Supplemental research retains its own source binding. It is not a raw
    # sweep posting and cannot contribute to discovery or posting totals.
    reviewed_research: tuple[ReviewedCase, ...] = ()

    @property
    def actionable(self) -> tuple[LiveReportNotice, ...]:
        return tuple(
            notice for notice in self.notices
            if notice.record.classification == LiveClassification.BID_NOW
            and notice.record.recommendation == LiveRecommendation.PURSUE
        )

    def verdict_counts(self) -> dict[str, int]:
        return dict(self.verdict_totals)


def _result(
    state: LiveReportState,
    client_name: str,
    scope_designator: str,
    *,
    problem: Optional[str] = None,
) -> LiveReportProjection:
    return LiveReportProjection(
        state=state,
        client_name=client_name,
        scope_designator=scope_designator,
        problem=problem,
    )


def _scope_for_report(searches: dict) -> tuple[Optional[Any], str, Optional[str]]:
    try:
        scope = scope_from_sweep(searches)
    except AssessLedgerError as exc:
        return None, "all", str(exc)
    return scope, _scope_designator(scope), None


def _unbound_scope_result(
    client_name: str,
    designator: str,
    problem: str,
    *,
    state_dir: Optional[str | Path],
) -> LiveReportProjection:
    """Resolve malformed scope evidence against exact-client pointer dormancy."""
    try:
        pointer_root = current_assess_pointer_path(
            client_name, "all", state_dir=state_dir).parent
        try:
            with os.scandir(pointer_root) as entries:
                has_client_pointer = any(
                    entry.name.endswith(".current.json") for entry in entries)
        except FileNotFoundError:
            if os.path.lexists(pointer_root):
                raise
            has_client_pointer = False
    except OSError as exc:
        return _result(
            LiveReportState.INVALID,
            client_name,
            designator,
            problem=("current strict ledger pointer state is unreadable: "
                     f"{exc}"),
        )
    if has_client_pointer:
        return _result(
            LiveReportState.INVALID,
            client_name,
            designator,
            problem="current strict ledger cannot bind this sweep: " + problem,
        )
    return _result(LiveReportState.ABSENT, client_name, designator)


def report_verdict(record: LiveSolicitation) -> str:
    """Translate strict recommendations into the established tally vocabulary."""
    if (record.classification == LiveClassification.BID_NOW
            and record.recommendation == LiveRecommendation.PURSUE):
        return "pursue"
    if record.recommendation == LiveRecommendation.MONITOR:
        return "monitor"
    if record.recommendation == LiveRecommendation.NO_BID:
        return "discard"
    if record.recommendation == LiveRecommendation.RESEARCH:
        return "research"
    return "unscreened"


def project_live_ledger(
    ledger: LiveSolicitationLedger,
    posting_index: dict[str, str],
    searches: dict,
    *,
    can_release: Optional[bool] = None,
    live_coverage_complete: Optional[bool] = None,
    reviewed_cases: Optional[ReviewedCases] = None,
) -> LiveReportProjection:
    """Re-join a validated ledger to raw SAM display metadata.

    Selection, classification, recommendation, deadline, family identity, and
    NOTICE evidence all come from ``ledger``. Raw sweep rows only supply
    presentation fields that are not carried by the strict contract, such as
    NAICS and set-aside labels.
    """
    results = searches.get("results")
    if not isinstance(results, dict):
        raise AssessLedgerError("report sweep is missing its results object")
    raw_rows = results.get("sam.gov")
    if not isinstance(raw_rows, list):
        raise AssessLedgerError("report sweep SAM collection is malformed")
    if not isinstance(posting_index, dict):
        raise AssessLedgerError("Assess posting index is malformed")

    by_id: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or "").strip()
        if not source_id:
            continue
        if source_id in by_id:
            raise AssessLedgerError(
                f"report sweep has duplicate SAM posting id {source_id}")
        by_id[source_id] = row

    grouped: dict[str, list[dict[str, Any]]] = {}
    for posting_id, record_id in posting_index.items():
        if not isinstance(posting_id, str) or not isinstance(record_id, str):
            raise AssessLedgerError("Assess posting index contains invalid entries")
        row = by_id.get(posting_id)
        if row is None:
            raise AssessLedgerError(
                f"Assess posting {posting_id} is absent from the exact report sweep")
        grouped.setdefault(record_id, []).append(row)

    reviewed_by_id: dict[str, ReviewedCase] = {}
    if reviewed_cases is not None:
        # Revalidate even an unvalidated model_copy; a supplied casebook can
        # never introduce BID_NOW or silently change the bound record.
        book = ReviewedCases.model_validate(reviewed_cases.model_dump(mode="python"))
        if book.client_name.casefold() != ledger.client_name.casefold():
            raise AssessLedgerError("reviewed cases belong to a different client")
        if book.cases and book.scope_designator != _scope_designator(ledger.scope):
            raise AssessLedgerError("reviewed cases do not match the report scope")
        reviewed_by_id = {case.record.notice_id: case for case in book.cases}

    notices: list[LiveReportNotice] = []
    reviewed_research: list[ReviewedCase] = []
    counts: dict[str, int] = {}
    ledger_ids = {record.record_id for record in ledger.records}
    unknown_families = set(grouped) - ledger_ids
    if unknown_families:
        raise AssessLedgerError(
            "Assess posting index names a family absent from the live ledger")
    for record in ledger.records:
        postings = sorted(grouped.get(record.record_id, []), key=_posting_order)
        current = by_id.get(record.notice_id)
        if (current is None and record.notice_id not in posting_index
                and not postings):
            reviewed = reviewed_by_id.get(record.notice_id)
            if reviewed is not None and reviewed.record == record:
                reviewed_research.append(reviewed)
                continue
        if current is None or posting_index.get(record.notice_id) != record.record_id:
            raise AssessLedgerError(
                f"Assess current posting {record.notice_id} is not bound to its family")
        if not postings or postings[-1].get("source_id") != record.notice_id:
            raise AssessLedgerError(
                f"Assess family {record.record_id} does not end at its current posting")
        bucket = report_verdict(record)
        # verdict_totals is the raw posting-level provenance tally. The board
        # remains family/solicitation-level, so an amendment family contributes
        # one pursuit card but every SAM posting to this internal tally.
        counts[bucket] = counts.get(bucket, 0) + len(postings)
        notices.append(LiveReportNotice(
            record=record,
            current=current,
            postings=tuple(postings),
        ))

    notices.sort(key=lambda row: (row.record.record_id, row.record.notice_id))
    return LiveReportProjection(
        state=LiveReportState.CURRENT,
        client_name=ledger.client_name,
        scope_designator=_scope_designator(ledger.scope),
        run_id=ledger.run_id,
        notices=tuple(notices),
        verdict_totals=tuple(sorted(counts.items())),
        can_release=can_release,
        live_coverage_complete=live_coverage_complete,
        reviewed_research=tuple(sorted(
            reviewed_research, key=lambda case: case.record.record_id)),
    )


def resolve_current_live_report(
    client_name: str,
    searches: dict,
    profile: Any,
    *,
    state_dir: Optional[str | Path] = None,
    review_dir: Optional[str | Path] = None,
    effective_date: Optional[date] = None,
) -> LiveReportProjection:
    """Resolve an exact current ledger, or return an explicit fallback state."""
    cutoff = effective_date or utc_today()
    scope, designator, scope_problem = _scope_for_report(searches)
    if scope_problem:
        # A malformed sweep cannot tell us which scope-specific pointer to
        # inspect.  Legacy fallback remains valid only for a client with no
        # cutover pointer at all; any exact-client pointer makes this stale
        # evidence fail closed instead of guessing the all-federal lane.
        return _unbound_scope_result(
            client_name, designator, scope_problem, state_dir=state_dir)
    try:
        pointer = current_assess_pointer_path(
            client_name, designator, state_dir=state_dir)
    except AssessLedgerError as exc:
        return _unbound_scope_result(
            client_name, designator, str(exc), state_dir=state_dir)
    if not pointer.exists():
        return _result(LiveReportState.ABSENT, client_name, designator)
    if profile is None:
        return _result(
            LiveReportState.INVALID, client_name, designator,
            problem="current strict ledger cannot bind an unreadable capability profile",
        )

    payload = load_current_assess_run(
        client_name, designator, state_dir=state_dir)
    if payload is None:
        return _result(
            LiveReportState.INVALID, client_name, designator,
            problem="current strict ledger pointer or immutable run is invalid",
        )
    try:
        run = AssessRun.model_validate(payload.get("run") or {})
        binding = payload.get("binding")
        if not isinstance(binding, dict):
            raise AssessLedgerError("current strict ledger has no evidence binding")
        if binding.get("scope_designator") != designator:
            raise AssessLedgerError("current strict ledger scope differs from this report")
        if binding.get("sweep_sha256") != digest(searches):
            raise AssessLedgerError("current strict ledger sweep evidence has changed")
        if binding.get("profile_sha256") != digest(profile):
            raise AssessLedgerError("current strict ledger capability profile has changed")
        expected_inputs = assess_projection_input_manifest(
            client_name, review_dir=review_dir)
        if payload.get("projection_inputs") != expected_inputs:
            raise AssessLedgerError(
                "current strict ledger review or reference inputs have changed")
        if run.client_name != client_name or run.live.client_name != client_name:
            raise AssessLedgerError("current strict ledger belongs to another client")
        sam_coverage = next((
            row for row in run.coverage
            if row.lane == SourceLane.LIVE and row.source == "sam.gov"
        ), None)
        if sam_coverage is None:
            raise AssessLedgerError("current strict ledger has no SAM coverage row")
        from agents.review import REVIEW_DIR
        reviewed_cases = load_cases(
            client_name, Path(review_dir or REVIEW_DIR),
            expected_sha256=expected_inputs["reviewed_cases_sha256"],
        )
        projection = project_live_ledger(
            run.live,
            payload.get("posting_index") or {},
            searches,
            can_release=run.can_release(),
            live_coverage_complete=(
                sam_coverage.status == CoverageStatus.COMPLETE),
            reviewed_cases=reviewed_cases,
        )
        expired = [
            notice.record.notice_id for notice in projection.actionable
            if (notice.record.response_deadline is None
                or notice.record.response_deadline <= cutoff)
        ]
        if expired:
            raise AssessLedgerError(
                "current strict ledger has BID_NOW deadline(s) no longer "
                "future: " + ", ".join(expired[:8]))
        return projection
    except (AssessLedgerError, TypeError, ValueError, OSError) as exc:
        return _result(
            LiveReportState.INVALID, client_name, designator, problem=str(exc))


def project_sweep_live_report(
    client_name: str,
    searches: dict,
    profile: Any,
    binding: dict,
    *,
    as_of: Any,
    requirement_reviews_payload: Optional[dict] = None,
) -> tuple[LiveReportProjection, tuple[str, ...]]:
    """Build an in-memory strict projection for pre-cutover parity review."""
    if searches.get("client") != client_name:
        raise AssessLedgerError(
            "strict parity sweep belongs to a different client")
    if getattr(profile, "client_name", None) != client_name:
        raise AssessLedgerError(
            "strict parity capability profile belongs to a different client")
    scope = scope_from_sweep(searches)
    if binding.get("scope_designator") != _scope_designator(scope):
        raise AssessLedgerError(
            "strict parity binding and sweep scope do not match")
    if binding.get("sweep_sha256") != digest(searches):
        raise AssessLedgerError("strict parity sweep binding has drifted")
    if binding.get("profile_sha256") != digest(profile):
        raise AssessLedgerError("strict parity profile binding has drifted")
    ledger, posting_index, _accepted, diagnostics = adapt_live_ledger(
        searches,
        profile,
        run_id="assess:parity:unpersisted",
        scope=scope,
        profile_version=str(binding.get("profile_sha256") or "parity"),
        as_of=as_of,
        requirement_reviews_payload=requirement_reviews_payload,
        binding=binding,
    )
    coverage = build_source_coverage(
        searches, live_diagnostics=tuple(diagnostics))
    sam_coverage = next(
        row for row in coverage
        if row.lane == SourceLane.LIVE and row.source == "sam.gov")
    projection = project_live_ledger(
        ledger,
        posting_index,
        searches,
        can_release=False,
        live_coverage_complete=(sam_coverage.status == CoverageStatus.COMPLETE),
    )
    return projection, tuple(diagnostics)
