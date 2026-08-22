"""Bounded production caller for Candidate Review event and vehicle research.

This module closes the Chunk 3A collection boundary.  It binds one approved
client/run/scope to immutable event and vehicle frames, a byte-exact standing
event registry, and explicit provider configurations.  Callers inject any
live or replay search functions; this module performs no network or model
calls of its own.

The output remains research-layer material.  Event and vehicle leads are not
verified report evidence, and this module deliberately does not build a
``CandidateReviewDocument`` or invoke a renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Iterable, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CoverageRecord,
    CoverageState,
)
from agents.candidate_review_v1.event_research import (
    EventDiscoveryResult,
    EventDiscoverySearcher,
    EventLead,
    EventQueryAttempt,
    EventQueryManifest,
    EventQuerySpec,
    EventResearchFrame,
    EventSearchResponse,
    build_event_query_manifest,
    run_event_discovery,
)
from agents.candidate_review_v1.event_watch import (
    EventWatchUniverse,
    WatchFrameTags,
    WatchTargetSelection,
    load_event_watch_universe,
    select_watch_targets,
    watch_universe_sha256,
)
from agents.candidate_review_v1.persistence import (
    GenerationCommit,
    GenerationReceipt,
    LoadedGeneration,
    load_current_generation,
    persist_generation,
)
from agents.candidate_review_v1.vehicle_watch import (
    VehicleCollectionResult,
    VehicleLead,
    VehicleQueryAttempt,
    VehicleQueryManifest,
    VehicleQuerySpec,
    VehicleSearchResponse,
    VehicleWatchFrame,
    VehicleWatchLane,
    VehicleWatchSearcher,
    build_vehicle_query_manifest,
    project_vehicle_watch_coverage,
    run_vehicle_watch_collection,
)


PIPELINE_SCHEMA_VERSION = "candidate_review_v1.research_pipeline.v1"
RESEARCH_SNAPSHOT_CHANGE_BUDGET = 8192

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ADAPTER_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class _PipelineContract(BaseModel):
    """Strict immutable boundary for production-caller state."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


def _require_sha256(value: str, label: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _stable_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProviderMode(str, Enum):
    """How an injected adapter may obtain its results."""

    LIVE = "live"
    REPLAY = "replay"
    UNAVAILABLE = "unavailable"


class ProviderBinding(_PipelineContract):
    """Exact adapter/configuration identity for one collection lane."""

    adapter_name: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=128)
    config_sha256: str
    mode: ProviderMode

    @model_validator(mode="after")
    def _provider_identity_is_canonical(self) -> "ProviderBinding":
        if _ADAPTER_RE.fullmatch(self.adapter_name) is None:
            raise ValueError("adapter_name must be a canonical lowercase token")
        if any(character.isspace() for character in self.adapter_version):
            raise ValueError("adapter_version cannot contain whitespace")
        _require_sha256(self.config_sha256, "provider config_sha256")
        return self


class ResearchProviderBindings(_PipelineContract):
    """Distinct event and vehicle adapters bound into generation basis."""

    event: ProviderBinding
    vehicle: ProviderBinding


class EventWatchCheck(_PipelineContract):
    """Canonical immutable form of one caller-supplied cadence check."""

    watch_target_id: str = Field(min_length=1)
    checked_at: datetime

    @model_validator(mode="after")
    def _check_has_an_aware_time(self) -> "EventWatchCheck":
        _require_aware(self.checked_at, "event watch checked_at")
        return self


class EventWatchCheckProjection(_PipelineContract):
    """Next-run cadence state after one complete event query census."""

    plan_sha256: str
    prior_checks: tuple[EventWatchCheck, ...]
    next_checks: tuple[EventWatchCheck, ...]
    advanced_target_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _projection_is_ordered_and_monotonic(self) -> "EventWatchCheckProjection":
        _require_sha256(self.plan_sha256, "watch check plan_sha256")
        for label, rows in (
            ("prior", self.prior_checks),
            ("next", self.next_checks),
        ):
            identifiers = tuple(row.watch_target_id for row in rows)
            if identifiers != tuple(sorted(identifiers)):
                raise ValueError(f"{label} watch checks must be ordered")
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} watch checks must be unique")
        if self.advanced_target_ids != tuple(sorted(
            self.advanced_target_ids
        )):
            raise ValueError("advanced watch target IDs must be ordered")
        if len(self.advanced_target_ids) != len(set(
            self.advanced_target_ids
        )):
            raise ValueError("advanced watch target IDs must be unique")
        prior = {row.watch_target_id: row.checked_at for row in self.prior_checks}
        next_rows = {
            row.watch_target_id: row.checked_at for row in self.next_checks
        }
        advanced = set(self.advanced_target_ids)
        if not advanced <= set(next_rows):
            raise ValueError("advanced watch target is absent from next checks")
        for target_id, prior_time in prior.items():
            next_time = next_rows.get(target_id)
            if next_time is None or next_time < prior_time:
                raise ValueError("next watch checks cannot lose or rewind state")
            if target_id not in advanced and next_time != prior_time:
                raise ValueError("unadvanced watch check changed its timestamp")
        if set(next_rows) - set(prior) != advanced - set(prior):
            raise ValueError("new watch checks must be declared advanced")
        return self

    def as_mapping(self) -> dict[str, datetime]:
        """Return a fresh mapping suitable for the next planning call."""

        return {
            row.watch_target_id: row.checked_at for row in self.next_checks
        }


class SnapshotLane(str, Enum):
    EVENT = "event"
    VEHICLE = "vehicle"


class SnapshotChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class SnapshotDiffState(str, Enum):
    BASELINE = "baseline"
    COMPARED = "compared"


class ResearchSnapshotChange(_PipelineContract):
    """One research-only change; verification is required before ticker use."""

    change_id: str
    lane: SnapshotLane
    kind: SnapshotChangeKind
    identity: str = Field(min_length=1)
    previous_sha256: Optional[str] = None
    current_sha256: Optional[str] = None
    requires_verification: Literal[True] = True
    creates_ticker: Literal[False] = False

    @model_validator(mode="after")
    def _change_has_the_exact_digest_shape(self) -> "ResearchSnapshotChange":
        for label, value in (
            ("previous_sha256", self.previous_sha256),
            ("current_sha256", self.current_sha256),
        ):
            if value is not None:
                _require_sha256(value, label)
        if self.kind is SnapshotChangeKind.ADDED:
            valid = self.previous_sha256 is None and self.current_sha256 is not None
        elif self.kind is SnapshotChangeKind.REMOVED:
            valid = self.previous_sha256 is not None and self.current_sha256 is None
        else:
            valid = (
                self.previous_sha256 is not None
                and self.current_sha256 is not None
                and self.previous_sha256 != self.current_sha256
            )
        if not valid:
            raise ValueError("snapshot change digest shape differs from its kind")
        expected_id = _stable_digest({
            "lane": self.lane.value,
            "kind": self.kind.value,
            "identity": self.identity,
            "previous_sha256": self.previous_sha256,
            "current_sha256": self.current_sha256,
        })
        if self.change_id != expected_id:
            raise ValueError("snapshot change_id does not match its content")
        return self


class ResearchSnapshotDiff(_PipelineContract):
    """Bounded comparison queue for later evidence verification and tickers."""

    plan_sha256: str
    binding: ArtifactBinding
    generated_at: datetime
    state: SnapshotDiffState
    previous_run_id: Optional[str] = None
    previous_basis_sha256: Optional[str] = None
    event_manifest_id: str
    vehicle_manifest_id: str
    changes: tuple[ResearchSnapshotChange, ...] = Field(
        max_length=RESEARCH_SNAPSHOT_CHANGE_BUDGET
    )
    removal_coverage_lanes: tuple[SnapshotLane, ...] = Field(
        default_factory=tuple,
    )
    ticker_review_change_ids: tuple[str, ...] = Field(
        max_length=RESEARCH_SNAPSHOT_CHANGE_BUDGET
    )
    publication_boundary: Literal[
        "research_only_requires_verified_evidence"
    ] = "research_only_requires_verified_evidence"

    @model_validator(mode="after")
    def _diff_is_bounded_and_ordered(self) -> "ResearchSnapshotDiff":
        _require_sha256(self.plan_sha256, "snapshot diff plan_sha256")
        _require_aware(self.generated_at, "snapshot diff generated_at")
        if self.previous_basis_sha256 is not None:
            _require_sha256(
                self.previous_basis_sha256,
                "snapshot diff previous_basis_sha256",
            )
        if (self.previous_run_id is None) != (
            self.previous_basis_sha256 is None
        ):
            raise ValueError("snapshot diff prior reference is incomplete")
        if self.state is SnapshotDiffState.BASELINE and self.changes:
            raise ValueError("baseline snapshot diff cannot assert changes")
        if len(self.removal_coverage_lanes) \
                != len(set(self.removal_coverage_lanes)):
            raise ValueError("snapshot removal-coverage lanes must be unique")
        if self.removal_coverage_lanes != tuple(sorted(
            self.removal_coverage_lanes,
            key=list(SnapshotLane).index,
        )):
            raise ValueError("snapshot removal-coverage lanes must be canonical")
        if any(
            row.kind is SnapshotChangeKind.REMOVED
            and row.lane not in self.removal_coverage_lanes
            for row in self.changes
        ):
            raise ValueError(
                "snapshot removal lacks a complete current coverage census"
            )
        ordered = tuple(sorted(
            self.changes,
            key=lambda row: (
                row.lane.value,
                list(SnapshotChangeKind).index(row.kind),
                row.identity,
            ),
        ))
        if self.changes != ordered:
            raise ValueError("snapshot changes must be canonical")
        identifiers = tuple(row.change_id for row in self.changes)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("snapshot change IDs must be unique")
        if self.ticker_review_change_ids != identifiers:
            raise ValueError(
                "ticker review queue must contain every research change exactly"
            )
        return self


def _plan_payload(
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    event_research_frame: EventResearchFrame,
    watch_frame_tags: WatchFrameTags,
    vehicle_watch_frame: VehicleWatchFrame,
    event_last_checked: tuple[EventWatchCheck, ...],
    event_watch_universe_sha256: str,
    event_watch_universe: EventWatchUniverse,
    event_watch_selection: WatchTargetSelection,
    event_query_manifest: EventQueryManifest,
    vehicle_query_manifest: VehicleQueryManifest,
    providers: ResearchProviderBindings,
) -> dict[str, object]:
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "binding": binding.model_dump(mode="json"),
        "as_of": as_of.isoformat(),
        "event_research_frame": event_research_frame.model_dump(mode="json"),
        "watch_frame_tags": watch_frame_tags.model_dump(mode="json"),
        "vehicle_watch_frame": vehicle_watch_frame.model_dump(mode="json"),
        "event_last_checked": [
            row.model_dump(mode="json") for row in event_last_checked
        ],
        "event_watch_universe_sha256": event_watch_universe_sha256,
        "event_watch_universe": event_watch_universe.model_dump(mode="json"),
        "event_watch_selection": event_watch_selection.model_dump(mode="json"),
        "event_query_manifest": event_query_manifest.model_dump(mode="json"),
        "vehicle_query_manifest": vehicle_query_manifest.model_dump(mode="json"),
        "providers": providers.model_dump(mode="json"),
    }


class CandidateReviewGenerationPlan(_PipelineContract):
    """Fully resolved, recomputable basis for one research generation."""

    schema_version: str = PIPELINE_SCHEMA_VERSION
    plan_sha256: str
    binding: ArtifactBinding
    as_of: datetime
    event_research_frame: EventResearchFrame
    watch_frame_tags: WatchFrameTags
    vehicle_watch_frame: VehicleWatchFrame
    event_last_checked: tuple[EventWatchCheck, ...] = Field(
        default_factory=tuple
    )
    event_watch_universe_sha256: str
    event_watch_universe: EventWatchUniverse
    event_watch_selection: WatchTargetSelection
    event_query_manifest: EventQueryManifest
    vehicle_query_manifest: VehicleQueryManifest
    providers: ResearchProviderBindings

    @model_validator(mode="after")
    def _plan_is_closed_and_recomputable(self) -> "CandidateReviewGenerationPlan":
        if self.schema_version != PIPELINE_SCHEMA_VERSION:
            raise ValueError("research pipeline schema version is unsupported")
        _require_sha256(self.plan_sha256, "pipeline plan_sha256")
        _require_sha256(
            self.event_watch_universe_sha256,
            "event watch universe SHA-256",
        )
        _require_aware(self.as_of, "pipeline as_of")
        if self.event_research_frame.binding != self.binding:
            raise ValueError("event research frame binding does not match run")
        if self.vehicle_watch_frame.binding != self.binding:
            raise ValueError("vehicle watch frame binding does not match run")
        if self.event_query_manifest.binding != self.binding:
            raise ValueError("event query manifest binding does not match run")
        if self.vehicle_query_manifest.binding != self.binding:
            raise ValueError("vehicle query manifest binding does not match run")
        if self.event_query_manifest.watch_universe_sha256 \
                != self.event_watch_universe_sha256:
            raise ValueError("event manifest uses another watch universe")
        if self.event_query_manifest.frame_revision_sha256 \
                != self.event_research_frame.revision_sha256:
            raise ValueError("event manifest uses another approved frame")
        if self.vehicle_query_manifest.frame_sha256 \
                != self.vehicle_watch_frame.frame_sha256:
            raise ValueError("vehicle manifest uses another approved frame")
        if self.vehicle_query_manifest.as_of != self.as_of:
            raise ValueError("vehicle manifest uses another as_of")
        if self.event_query_manifest.window_start != self.as_of.date():
            raise ValueError("event manifest uses another as_of date")
        check_ids = tuple(row.watch_target_id for row in self.event_last_checked)
        if check_ids != tuple(sorted(check_ids)):
            raise ValueError("event watch checks must be ordered by target ID")
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("event watch checks must be unique")
        expected_selection = select_watch_targets(
            self.event_watch_universe,
            self.watch_frame_tags,
            as_of=self.as_of,
            last_checked_at={
                row.watch_target_id: row.checked_at
                for row in self.event_last_checked
            },
        )
        if self.event_watch_selection != expected_selection:
            raise ValueError(
                "event watch selection does not match its embedded inputs"
            )
        expected_event_manifest = build_event_query_manifest(
            self.event_research_frame,
            self.as_of,
            watch_selection=expected_selection,
            watch_universe_digest=self.event_watch_universe_sha256,
        )
        if self.event_query_manifest != expected_event_manifest:
            raise ValueError(
                "event query manifest does not match its embedded inputs"
            )
        expected_vehicle_manifest = build_vehicle_query_manifest(
            self.vehicle_watch_frame,
            self.as_of,
        )
        if self.vehicle_query_manifest != expected_vehicle_manifest:
            raise ValueError(
                "vehicle query manifest does not match its embedded inputs"
            )
        expected = _stable_digest(_plan_payload(
            binding=self.binding,
            as_of=self.as_of,
            event_research_frame=self.event_research_frame,
            watch_frame_tags=self.watch_frame_tags,
            vehicle_watch_frame=self.vehicle_watch_frame,
            event_last_checked=self.event_last_checked,
            event_watch_universe_sha256=self.event_watch_universe_sha256,
            event_watch_universe=self.event_watch_universe,
            event_watch_selection=self.event_watch_selection,
            event_query_manifest=self.event_query_manifest,
            vehicle_query_manifest=self.vehicle_query_manifest,
            providers=self.providers,
        ))
        if self.plan_sha256 != expected:
            raise ValueError("pipeline plan_sha256 does not match its content")
        return self

    def registry_payloads(self) -> dict[str, dict]:
        """Return fresh JSON payloads for the generation registry group."""

        return {
            "event-watch-universe": {
                "schema_version": PIPELINE_SCHEMA_VERSION,
                "registry_sha256": self.event_watch_universe_sha256,
                "universe": self.event_watch_universe.model_dump(mode="json"),
            },
            "event-watch-selection": {
                "schema_version": PIPELINE_SCHEMA_VERSION,
                "as_of": self.as_of.isoformat(),
                "frame_tags": self.watch_frame_tags.model_dump(mode="json"),
                "last_checked": [
                    row.model_dump(mode="json")
                    for row in self.event_last_checked
                ],
                "selection": self.event_watch_selection.model_dump(mode="json"),
            },
        }

    def manifest_payloads(self) -> dict[str, dict]:
        """Return basis-bearing input, provider, and query manifests."""

        return {
            "pipeline-inputs": {
                "schema_version": self.schema_version,
                "plan_sha256": self.plan_sha256,
                "binding": self.binding.model_dump(mode="json"),
                "as_of": self.as_of.isoformat(),
                "event_research_frame": self.event_research_frame.model_dump(
                    mode="json"
                ),
                "watch_frame_tags": self.watch_frame_tags.model_dump(
                    mode="json"
                ),
                "vehicle_watch_frame": self.vehicle_watch_frame.model_dump(
                    mode="json"
                ),
            },
            "provider-bindings": {
                "schema_version": self.schema_version,
                "providers": self.providers.model_dump(mode="json"),
            },
            "snapshot-diff-basis": {
                "schema_version": self.schema_version,
                "plan_sha256": self.plan_sha256,
                "previous_generation": None,
            },
            "event-query-manifest": self.event_query_manifest.model_dump(
                mode="json"
            ),
            "vehicle-query-manifest": self.vehicle_query_manifest.model_dump(
                mode="json"
            ),
        }


def _canonical_checks(
    values: Mapping[str, datetime],
) -> tuple[EventWatchCheck, ...]:
    if not isinstance(values, Mapping):
        raise TypeError("event_last_checked_at must be a mapping")
    return tuple(
        EventWatchCheck(watch_target_id=target_id, checked_at=checked_at)
        for target_id, checked_at in sorted(values.items())
    )


def plan_candidate_review_generation(
    binding: ArtifactBinding,
    event_research_frame: EventResearchFrame,
    watch_frame_tags: WatchFrameTags,
    vehicle_watch_frame: VehicleWatchFrame,
    *,
    as_of: datetime,
    event_last_checked_at: Mapping[str, datetime],
    registry_path: Path | str | None,
    providers: ResearchProviderBindings,
) -> CandidateReviewGenerationPlan:
    """Resolve and validate the exact basis without executing providers."""

    _require_aware(as_of, "pipeline as_of")
    if event_research_frame.binding != binding:
        raise ValueError("event research frame binding does not match run")
    if vehicle_watch_frame.binding != binding:
        raise ValueError("vehicle watch frame binding does not match run")
    checks = _canonical_checks(event_last_checked_at)
    check_mapping = {
        row.watch_target_id: row.checked_at for row in checks
    }

    # Hash before and after validation so a concurrent registry replacement
    # cannot silently bind the loaded model to another byte identity.
    first_digest = watch_universe_sha256(registry_path)
    universe = load_event_watch_universe(registry_path)
    second_digest = watch_universe_sha256(registry_path)
    if first_digest != second_digest:
        raise ValueError("event watch registry changed while it was loaded")
    selection = select_watch_targets(
        universe,
        watch_frame_tags,
        as_of=as_of,
        last_checked_at=check_mapping,
    )
    event_manifest = build_event_query_manifest(
        event_research_frame,
        as_of,
        watch_selection=selection,
        watch_universe_digest=first_digest,
    )
    vehicle_manifest = build_vehicle_query_manifest(vehicle_watch_frame, as_of)
    values = dict(
        binding=binding,
        as_of=as_of,
        event_research_frame=event_research_frame,
        watch_frame_tags=watch_frame_tags,
        vehicle_watch_frame=vehicle_watch_frame,
        event_last_checked=checks,
        event_watch_universe_sha256=first_digest,
        event_watch_universe=universe,
        event_watch_selection=selection,
        event_query_manifest=event_manifest,
        vehicle_query_manifest=vehicle_manifest,
        providers=providers,
    )
    return CandidateReviewGenerationPlan(
        schema_version=PIPELINE_SCHEMA_VERSION,
        plan_sha256=_stable_digest(_plan_payload(**values)),
        **values,
    )


def _unavailable_event_result(
    manifest: EventQueryManifest,
    as_of: datetime,
) -> EventDiscoveryResult:
    attempts: list[EventQueryAttempt] = []
    for query in manifest.queries:
        if query.required:
            state = CoverageState.NOT_RUN
            detail = (
                "No event discovery provider was available for this generation."
            )
        elif query.nonrequired_reason == "scope_excluded" \
                and query.watch_target_id is not None:
            state = CoverageState.SCOPE_EXCLUDED
            detail = (
                "This standing watch target is outside the approved client frame."
            )
        else:
            state = CoverageState.SCOPE_EXCLUDED
            detail = "No approved inputs were supplied for this query family."
        attempts.append(EventQueryAttempt(
            query_id=query.query_id,
            state=state,
            attempted_at=None,
            records_returned=0,
            lead_ids=(),
            public_detail=detail,
        ))
    return EventDiscoveryResult(
        manifest=manifest,
        attempted_at=as_of,
        leads=(),
        attempts=tuple(attempts),
    )


def _unavailable_vehicle_result(
    manifest: VehicleQueryManifest,
    as_of: datetime,
) -> VehicleCollectionResult:
    attempts = tuple(
        VehicleQueryAttempt(
            query_id=query.query_id,
            state=(
                CoverageState.NOT_RUN
                if query.required
                else CoverageState.SCOPE_EXCLUDED
            ),
            attempted_at=None,
            records_returned=0,
            lead_ids=(),
            returned_zero=False,
            public_detail=(
                "No vehicle collection provider was available for this "
                "generation."
                if query.required
                else (
                    "No operator-supplied restricted export was present."
                    if query.lane
                    is VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT
                    else "No approved target was present for this lane."
                )
            ),
        )
        for query in manifest.queries
    )
    return VehicleCollectionResult(
        manifest=manifest,
        collected_at=as_of,
        leads=(),
        attempts=attempts,
    )


def project_next_event_watch_checks(
    plan: CandidateReviewGenerationPlan,
    result: EventDiscoveryResult,
) -> EventWatchCheckProjection:
    """Advance cadence only after every query for a due target returned.

    A standing target may own several source-class queries.  Every applicable
    target is due on every run, and its check time advances to the latest
    attempt only when *all* of those required queries completed with
    ``RETURNED``.  Zero results still represent a completed check.  ``PARTIAL``,
    ``FAILED``, ``NOT_RUN``, missing attempts, or future timestamps retain the
    caller's prior value unchanged.
    """

    if result.manifest != plan.event_query_manifest:
        raise ValueError("event result belongs to another pipeline plan")
    attempts = {row.query_id: row for row in result.attempts}
    prior = {
        row.watch_target_id: row.checked_at for row in plan.event_last_checked
    }
    next_checks = dict(prior)
    advanced: list[str] = []
    for target_id in plan.event_query_manifest.search_due_watch_target_ids:
        queries = tuple(
            query for query in plan.event_query_manifest.queries
            if query.watch_target_id == target_id
        )
        if not queries or any(not query.required for query in queries):
            raise ValueError("search-due watch target has an incomplete census")
        target_attempts = tuple(attempts[query.query_id] for query in queries)
        if not all(
            attempt.state is CoverageState.RETURNED
            and attempt.attempted_at is not None
            for attempt in target_attempts
        ):
            continue
        checked_at = max(
            attempt.attempted_at for attempt in target_attempts
            if attempt.attempted_at is not None
        )
        if checked_at > plan.as_of:
            raise ValueError("event watch completion postdates pipeline as_of")
        if checked_at < prior.get(target_id, checked_at):
            continue
        next_checks[target_id] = checked_at
        advanced.append(target_id)
    return EventWatchCheckProjection(
        plan_sha256=plan.plan_sha256,
        prior_checks=plan.event_last_checked,
        next_checks=tuple(
            EventWatchCheck(watch_target_id=target_id, checked_at=checked_at)
            for target_id, checked_at in sorted(next_checks.items())
        ),
        advanced_target_ids=tuple(sorted(advanced)),
    )


def _event_snapshot(rows: tuple[EventLead, ...]) -> dict[str, str]:
    observations: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        url = str(row.url).casefold()
        identity = f"event:{_stable_digest({'url': url})}"
        observations.setdefault(identity, []).append({
            "url": url,
            "source_class": row.source_class.value,
            "title": row.title,
            "claimed_date": row.claimed_date,
            "claimed_location": row.claimed_location,
            "claimed_organizer": row.claimed_organizer,
        })
    return {
        identity: _stable_digest(sorted(
            values,
            key=lambda value: json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        ))
        for identity, values in sorted(observations.items())
    }


def _vehicle_snapshot(rows: tuple[VehicleLead, ...]) -> dict[str, str]:
    observations: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        identity_payload = {
            "source": row.source_identity.canonical_key,
            "parent": (
                row.parent_idv_identity.canonical_key
                if row.parent_idv_identity else None
            ),
            "order": (
                row.order_identity.canonical_key if row.order_identity else None
            ),
        }
        identity = f"vehicle:{_stable_digest(identity_payload)}"
        observations.setdefault(identity, []).append({
            **identity_payload,
            "lane": row.lane.value,
            "title": row.title,
            "source_url": str(row.source_url).casefold(),
        })
    return {
        identity: _stable_digest(sorted(
            values,
            key=lambda value: json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        ))
        for identity, values in sorted(observations.items())
    }


def _snapshot_changes(
    lane: SnapshotLane,
    previous: Mapping[str, str],
    current: Mapping[str, str],
    *,
    allow_removals: bool,
) -> tuple[ResearchSnapshotChange, ...]:
    values: list[ResearchSnapshotChange] = []
    for identity in sorted(set(previous) | set(current)):
        before = previous.get(identity)
        after = current.get(identity)
        if before == after:
            continue
        if before is None:
            kind = SnapshotChangeKind.ADDED
        elif after is None:
            if not allow_removals:
                continue
            kind = SnapshotChangeKind.REMOVED
        else:
            kind = SnapshotChangeKind.CHANGED
        content = {
            "lane": lane.value,
            "kind": kind.value,
            "identity": identity,
            "previous_sha256": before,
            "current_sha256": after,
        }
        values.append(ResearchSnapshotChange(
            change_id=_stable_digest(content),
            lane=lane,
            kind=kind,
            identity=identity,
            previous_sha256=before,
            current_sha256=after,
        ))
    return tuple(values)


def _event_removal_coverage_is_complete(
    previous: EventDiscoveryResult,
    current: EventDiscoveryResult,
) -> bool:
    def comparison_basis(result: EventDiscoveryResult) -> str:
        """Digest every search semantic while excluding per-run identity.

        Manifest IDs cannot be compared directly because their binding
        intentionally includes ``run_id`` and the evidence snapshot.  Those
        values change on a legitimate refresh.  Removal safety instead binds
        the stable client/scope/profile identity plus the complete approved
        frame revision, horizons, registry/selection partition, organizer
        trust boundary, and byte-derived query specifications.  A frame,
        registry, applicability, cadence, query-text, or query-ID change makes
        the event censuses non-comparable for removal purposes.
        """

        manifest = result.manifest
        binding = manifest.binding
        return _stable_digest({
            "version": manifest.version,
            "binding": {
                "client_id": binding.client_id,
                "client_name": binding.client_name,
                "scope_designator": binding.scope_designator,
                "scope_sha256": binding.scope_sha256,
                "profile_sha256": binding.profile_sha256,
            },
            "frame_revision_sha256": manifest.frame_revision_sha256,
            "window_start": manifest.window_start.isoformat(),
            "confirmed_event_through": (
                manifest.confirmed_event_through.isoformat()
            ),
            "flagship_event_through": (
                manifest.flagship_event_through.isoformat()
            ),
            "trusted_organizer_domains": manifest.trusted_organizer_domains,
            "watch_universe_sha256": manifest.watch_universe_sha256,
            "watch_target_ids": manifest.watch_target_ids,
            "applicable_watch_target_ids": (
                manifest.applicable_watch_target_ids
            ),
            "search_due_watch_target_ids": (
                manifest.search_due_watch_target_ids
            ),
            "cadence_deferred_watch_target_ids": (
                manifest.cadence_deferred_watch_target_ids
            ),
            "scope_excluded_watch_target_ids": (
                manifest.scope_excluded_watch_target_ids
            ),
            "queries": [
                query.model_dump(mode="json")
                for query in manifest.queries
            ],
        })

    required_ids = {
        query.query_id
        for query in current.manifest.queries
        if query.required
    }
    attempts = {row.query_id: row for row in current.attempts}
    return (
        bool(required_ids)
        and comparison_basis(previous) == comparison_basis(current)
        and all(
            attempts[query_id].state is CoverageState.RETURNED
            for query_id in required_ids
        )
    )


def _vehicle_removal_coverage_is_complete(
    previous: VehicleCollectionResult,
    current: VehicleCollectionResult,
) -> bool:
    def comparison_basis(result: VehicleCollectionResult) -> str:
        """Bind the approved vehicle frame and full query semantics."""

        manifest = result.manifest
        binding = manifest.binding
        return _stable_digest({
            "version": manifest.version,
            "binding": {
                "client_id": binding.client_id,
                "client_name": binding.client_name,
                "scope_designator": binding.scope_designator,
                "scope_sha256": binding.scope_sha256,
                "profile_sha256": binding.profile_sha256,
            },
            "frame_revision_sha256": manifest.frame_revision_sha256,
            "window_start": manifest.window_start.isoformat(),
            "queries": [
                query.model_dump(mode="json")
                for query in manifest.queries
            ],
        })

    required_ids = {
        query.query_id
        for query in current.manifest.queries
        if query.required
    }
    attempts = {row.query_id: row for row in current.attempts}
    return (
        bool(required_ids)
        and comparison_basis(previous) == comparison_basis(current)
        and all(
            attempts[query_id].state is CoverageState.RETURNED
            for query_id in required_ids
        )
    )


def _comparable_previous_results(
    plan: CandidateReviewGenerationPlan,
    previous: Optional[LoadedGeneration],
) -> Optional[tuple[EventDiscoveryResult, VehicleCollectionResult]]:
    if previous is None:
        return None
    prior_binding = previous.receipt.binding
    if prior_binding.client_id != plan.binding.client_id:
        raise ValueError("previous generation belongs to another client")
    if (
        prior_binding.scope_designator != plan.binding.scope_designator
        or prior_binding.scope_sha256 != plan.binding.scope_sha256
        or prior_binding.profile_sha256 != plan.binding.profile_sha256
    ):
        return None
    event_payload = previous.artifacts.get("event-discovery-result")
    vehicle_payload = previous.artifacts.get("vehicle-collection-result")
    if event_payload is None and vehicle_payload is None:
        return None
    if event_payload is None or vehicle_payload is None:
        raise ValueError("previous research generation is only partially present")
    return (
        EventDiscoveryResult.model_validate(event_payload),
        VehicleCollectionResult.model_validate(vehicle_payload),
    )


def build_research_snapshot_diff(
    plan: CandidateReviewGenerationPlan,
    event_result: EventDiscoveryResult,
    vehicle_result: VehicleCollectionResult,
    *,
    previous: Optional[LoadedGeneration],
) -> ResearchSnapshotDiff:
    """Compare stable lead identities without asserting publishable facts."""

    if event_result.manifest != plan.event_query_manifest:
        raise ValueError("event result belongs to another pipeline plan")
    if vehicle_result.manifest != plan.vehicle_query_manifest:
        raise ValueError("vehicle result belongs to another pipeline plan")
    prior = _comparable_previous_results(plan, previous)
    previous_run_id = previous.receipt.binding.run_id if previous else None
    previous_basis = previous.receipt.basis_sha256 if previous else None

    # An exact retry must retain the originally committed comparison rather
    # than compare the generation to itself and mutate baseline semantics.
    if previous is not None and previous.receipt.binding == plan.binding \
            and prior is not None and prior == (event_result, vehicle_result):
        payload = previous.artifacts.get("research-snapshot-diff")
        if payload is not None:
            existing = ResearchSnapshotDiff.model_validate_json(json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ))
            if existing.plan_sha256 == plan.plan_sha256:
                return existing

    if prior is None:
        state = SnapshotDiffState.BASELINE
        changes: tuple[ResearchSnapshotChange, ...] = ()
        removal_coverage_lanes: tuple[SnapshotLane, ...] = ()
    else:
        state = SnapshotDiffState.COMPARED
        previous_event, previous_vehicle = prior
        removal_coverage_lanes = tuple(
            lane
            for lane, complete in (
                (
                    SnapshotLane.EVENT,
                    _event_removal_coverage_is_complete(
                        previous_event,
                        event_result,
                    ),
                ),
                (
                    SnapshotLane.VEHICLE,
                    _vehicle_removal_coverage_is_complete(
                        previous_vehicle,
                        vehicle_result,
                    ),
                ),
            )
            if complete
        )
        changes = tuple(sorted(
            (
                *_snapshot_changes(
                    SnapshotLane.EVENT,
                    _event_snapshot(previous_event.leads),
                    _event_snapshot(event_result.leads),
                    allow_removals=(
                        SnapshotLane.EVENT in removal_coverage_lanes
                    ),
                ),
                *_snapshot_changes(
                    SnapshotLane.VEHICLE,
                    _vehicle_snapshot(previous_vehicle.leads),
                    _vehicle_snapshot(vehicle_result.leads),
                    allow_removals=(
                        SnapshotLane.VEHICLE in removal_coverage_lanes
                    ),
                ),
            ),
            key=lambda row: (
                row.lane.value,
                list(SnapshotChangeKind).index(row.kind),
                row.identity,
            ),
        ))
    return ResearchSnapshotDiff(
        plan_sha256=plan.plan_sha256,
        binding=plan.binding,
        generated_at=plan.as_of,
        state=state,
        previous_run_id=previous_run_id,
        previous_basis_sha256=previous_basis,
        event_manifest_id=plan.event_query_manifest.manifest_id,
        vehicle_manifest_id=plan.vehicle_query_manifest.manifest_id,
        changes=changes,
        removal_coverage_lanes=removal_coverage_lanes,
        ticker_review_change_ids=tuple(row.change_id for row in changes),
    )


def _snapshot_diff_basis_payload(
    plan: CandidateReviewGenerationPlan,
    previous: Optional[LoadedGeneration],
) -> dict:
    """Bind the implicit prior snapshot into the persisted generation basis."""

    if previous is not None and previous.receipt.binding == plan.binding:
        payload = previous.manifests.get("snapshot-diff-basis")
        if not isinstance(payload, dict):
            raise ValueError("reused generation has no snapshot diff basis")
        if (
            payload.get("schema_version") != PIPELINE_SCHEMA_VERSION
            or payload.get("plan_sha256") != plan.plan_sha256
        ):
            raise ValueError("reused snapshot diff basis does not match plan")
        return json.loads(json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ))
    reference = None
    if previous is not None:
        reference = {
            "binding": previous.receipt.binding.model_dump(mode="json"),
            "basis_sha256": previous.receipt.basis_sha256,
            "receipt_sha256": previous.pointer.receipt_sha256,
            "committed_at": previous.receipt.committed_at.isoformat(),
        }
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "plan_sha256": plan.plan_sha256,
        "previous_generation": reference,
    }


EventSearcher = Union[
    EventDiscoverySearcher,
    Callable[[EventQuerySpec], Union[
        EventSearchResponse,
        Iterable[Union[EventLead, Mapping[str, object]]],
    ]],
]
VehicleSearcher = Union[
    VehicleWatchSearcher,
    Callable[[VehicleQuerySpec], Union[
        VehicleSearchResponse,
        Iterable[Union[VehicleLead, Mapping[str, object]]],
    ]],
]


def _validate_runtime_provider(
    binding: ProviderBinding,
    searcher: object,
    lane: str,
) -> None:
    available = searcher is not None
    if binding.mode is ProviderMode.UNAVAILABLE and available:
        raise ValueError(f"{lane} provider is unavailable but a searcher was supplied")
    if binding.mode is not ProviderMode.UNAVAILABLE and not available:
        raise ValueError(f"{lane} provider requires its bound searcher")
    if available and not callable(searcher):
        raise TypeError(f"{lane} searcher must be callable")


@dataclass(frozen=True)
class CandidateReviewGeneration:
    """Research outputs and the immutable persistence commit that closed them."""

    plan: CandidateReviewGenerationPlan
    event_result: EventDiscoveryResult
    vehicle_result: VehicleCollectionResult
    vehicle_coverage: tuple[CoverageRecord, ...]
    next_event_watch_checks: EventWatchCheckProjection
    snapshot_diff: ResearchSnapshotDiff
    commit: GenerationCommit

    @property
    def receipt(self) -> GenerationReceipt:
        return self.commit.generation.receipt


def execute_candidate_review_generation(
    plan: CandidateReviewGenerationPlan,
    *,
    event_searcher: Optional[EventSearcher] = None,
    vehicle_searcher: Optional[VehicleSearcher] = None,
    state_root: Path | str | None = None,
    committed_at: Optional[datetime] = None,
    extra_artifacts: Optional[Callable[[], Mapping[str, object]]] = None,
) -> CandidateReviewGeneration:
    """Execute injected providers, close coverage, and commit the generation.

    ``extra_artifacts`` is an optional zero-argument callable evaluated AFTER
    the searchers ran, so a live provider can persist collection-time fetch
    results (for example verified notice blocks) into the same digest-bound
    generation the document press replays.  Names may not collide with the
    standard artifact set.
    """

    _validate_runtime_provider(
        plan.providers.event,
        event_searcher,
        "event",
    )
    _validate_runtime_provider(
        plan.providers.vehicle,
        vehicle_searcher,
        "vehicle",
    )
    commit_time = committed_at or plan.as_of
    _require_aware(commit_time, "pipeline committed_at")
    if commit_time < plan.as_of:
        raise ValueError("pipeline committed_at cannot predate as_of")
    previous = load_current_generation(
        plan.binding.client_id,
        state_root=state_root,
    )
    event_result = (
        _unavailable_event_result(plan.event_query_manifest, plan.as_of)
        if event_searcher is None
        else run_event_discovery(
            plan.event_query_manifest,
            event_searcher,
            plan.as_of,
        )
    )
    vehicle_result = (
        _unavailable_vehicle_result(plan.vehicle_query_manifest, plan.as_of)
        if vehicle_searcher is None
        else run_vehicle_watch_collection(
            plan.vehicle_query_manifest,
            vehicle_searcher,
            plan.as_of,
        )
    )
    vehicle_coverage = project_vehicle_watch_coverage(vehicle_result)
    next_event_watch_checks = project_next_event_watch_checks(
        plan,
        event_result,
    )
    snapshot_diff = build_research_snapshot_diff(
        plan,
        event_result,
        vehicle_result,
        previous=previous,
    )
    artifacts = {
        "event-discovery-result": event_result.model_dump(mode="json"),
        "event-watch-checks-next": next_event_watch_checks.model_dump(
            mode="json"
        ),
        "research-snapshot-diff": snapshot_diff.model_dump(mode="json"),
        "vehicle-collection-result": vehicle_result.model_dump(mode="json"),
        "vehicle-watch-coverage": {
            "schema_version": PIPELINE_SCHEMA_VERSION,
            "binding": plan.binding.model_dump(mode="json"),
            "query_manifest_id": plan.vehicle_query_manifest.manifest_id,
            "coverage": [
                row.model_dump(mode="json") for row in vehicle_coverage
            ],
        },
    }
    if extra_artifacts is not None:
        for name, payload in extra_artifacts().items():
            if name in artifacts:
                raise ValueError(
                    f"extra artifact {name} collides with a standard artifact")
            artifacts[name] = payload
    manifests = plan.manifest_payloads()
    manifests["snapshot-diff-basis"] = _snapshot_diff_basis_payload(
        plan,
        previous,
    )
    commit = persist_generation(
        plan.binding,
        registries=plan.registry_payloads(),
        manifests=manifests,
        artifacts=artifacts,
        state_root=state_root,
        committed_at=commit_time,
    )
    return CandidateReviewGeneration(
        plan=plan,
        event_result=event_result,
        vehicle_result=vehicle_result,
        vehicle_coverage=vehicle_coverage,
        next_event_watch_checks=next_event_watch_checks,
        snapshot_diff=snapshot_diff,
        commit=commit,
    )


def generate_candidate_review_generation(
    binding: ArtifactBinding,
    event_research_frame: EventResearchFrame,
    watch_frame_tags: WatchFrameTags,
    vehicle_watch_frame: VehicleWatchFrame,
    *,
    as_of: datetime,
    event_last_checked_at: Mapping[str, datetime],
    registry_path: Path | str | None,
    providers: ResearchProviderBindings,
    event_searcher: Optional[EventSearcher] = None,
    vehicle_searcher: Optional[VehicleSearcher] = None,
    state_root: Path | str | None = None,
    committed_at: Optional[datetime] = None,
    extra_artifacts: Optional[Callable[[], Mapping[str, object]]] = None,
) -> CandidateReviewGeneration:
    """Plan and commit one bounded event/vehicle research generation."""

    plan = plan_candidate_review_generation(
        binding,
        event_research_frame,
        watch_frame_tags,
        vehicle_watch_frame,
        as_of=as_of,
        event_last_checked_at=event_last_checked_at,
        registry_path=registry_path,
        providers=providers,
    )
    return execute_candidate_review_generation(
        plan,
        event_searcher=event_searcher,
        vehicle_searcher=vehicle_searcher,
        state_root=state_root,
        committed_at=committed_at,
        extra_artifacts=extra_artifacts,
    )


__all__ = (
    "PIPELINE_SCHEMA_VERSION",
    "RESEARCH_SNAPSHOT_CHANGE_BUDGET",
    "CandidateReviewGeneration",
    "CandidateReviewGenerationPlan",
    "EventWatchCheck",
    "EventWatchCheckProjection",
    "ProviderBinding",
    "ProviderMode",
    "ResearchSnapshotChange",
    "ResearchSnapshotDiff",
    "ResearchProviderBindings",
    "SnapshotChangeKind",
    "SnapshotDiffState",
    "SnapshotLane",
    "build_research_snapshot_diff",
    "execute_candidate_review_generation",
    "generate_candidate_review_generation",
    "plan_candidate_review_generation",
    "project_next_event_watch_checks",
)
