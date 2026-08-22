"""Deterministic, offline replay providers for Candidate Review research.

Replay is an explicit input boundary, not a substitute for source research.
The event adapter consumes a strict, run- and manifest-bound response artifact.
The vehicle adapter only replays exact structured rows already present in the
current gate-designated sweep.  Neither adapter performs network or model
calls, verifies a lead, creates a report candidate, or publishes a ticker row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Literal, Mapping, Optional
from urllib.parse import quote, urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CoverageState,
    SourceIdentity,
)
from agents.candidate_review_v1.event_research import (
    EventQueryManifest,
    EventQuerySpec,
    EventSearchResponse,
)
from agents.candidate_review_v1.pipeline import (
    ProviderBinding,
    ProviderMode,
    ResearchProviderBindings,
)
from agents.candidate_review_v1.vehicle_watch import (
    VehicleLead,
    VehicleQueryManifest,
    VehicleQuerySpec,
    VehicleSearchResponse,
    VehicleTargetKind,
    VehicleWatchLane,
)


REPLAY_PROVIDER_CONFIG_VERSION = "candidate_review_v1.replay_config.v1"
EVENT_REPLAY_ARTIFACT_VERSION = "candidate_review_v1.event_replay.v1"
REPLAY_SOURCE_BUNDLE_VERSION = \
    "candidate_review_v1.replay_source_bundle.v1"
EVENT_REPLAY_SOURCE_VERSION = "candidate_review_v1.event_replay_source.v1"
REPLAY_ADAPTER_VERSION = "1.0.0"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_VEHICLE_LANES = frozenset({
    VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
    VehicleWatchLane.USASPENDING_IDV,
    VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
})


class ReplayProviderError(ValueError):
    """A replay input is not exactly bound to the requested generation."""


class _ReplayContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _require_sha256(value: str, label: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_sha256(value: object) -> str:
    material = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return _sha256(material)


def _decode_strict_json(value: bytes, *, label: str) -> object:
    """Decode JSON while rejecting duplicate object keys at every depth."""

    def object_from_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ReplayProviderError(
                    f"{label} contains duplicate object key {key!r}"
                )
            result[key] = item
        return result

    try:
        return json.loads(value, object_pairs_hook=object_from_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayProviderError(f"{label} is not valid UTF-8 JSON") from exc


def _aware_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ReplayProviderError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayProviderError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReplayProviderError(f"{label} must be timezone-aware")
    return parsed


def _exact_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReplayProviderError(f"{label} must be an exact nonblank string")
    return value


class ReplayFileReference(_ReplayContract):
    path: str = Field(min_length=1)
    sha256: str

    @model_validator(mode="after")
    def _reference_is_exact(self) -> "ReplayFileReference":
        _require_sha256(self.sha256, "replay file SHA-256")
        if "\n" in self.path or "\r" in self.path:
            raise ValueError("replay file path cannot contain line breaks")
        return self


class CurrentSweepReplay(_ReplayContract):
    source: Literal["current_sweep"] = "current_sweep"
    sweep_sha256: str

    @model_validator(mode="after")
    def _sweep_is_exact(self) -> "CurrentSweepReplay":
        _require_sha256(self.sweep_sha256, "replay sweep SHA-256")
        return self


class CurrentSweepSource(_ReplayContract):
    """Enable rebinding to the exact sweep observed at watch execution."""

    source: Literal["current_sweep"] = "current_sweep"


class ReplayProviderConfig(_ReplayContract):
    """Run-bound config selected by ``--replay-config`` at the CLI seam."""

    schema_version: str = REPLAY_PROVIDER_CONFIG_VERSION
    binding: ArtifactBinding
    event_manifest_id: str = Field(min_length=1)
    vehicle_manifest_id: str = Field(min_length=1)
    event: Optional[ReplayFileReference] = None
    vehicle: Optional[CurrentSweepReplay] = None

    @model_validator(mode="after")
    def _config_has_a_replay_lane(self) -> "ReplayProviderConfig":
        if self.schema_version != REPLAY_PROVIDER_CONFIG_VERSION:
            raise ValueError("replay provider config schema is unsupported")
        if self.event is None and self.vehicle is None:
            raise ValueError("replay provider config enables no provider lane")
        return self


class ReplaySourceBundleConfig(_ReplayContract):
    """Immutable client source selection, rebound after the mandatory re-pull.

    Unlike :class:`ReplayProviderConfig`, this is deliberately not run-bound.
    It names exact immutable event bytes and/or authorizes the already
    sanctioned current sweep.  The loader binds those sources to the current
    binding and manifests before any provider can execute.
    """

    schema_version: str = REPLAY_SOURCE_BUNDLE_VERSION
    client_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    client_name: str = Field(min_length=1)
    event: Optional[ReplayFileReference] = None
    vehicle: Optional[CurrentSweepSource] = None

    @model_validator(mode="after")
    def _bundle_has_a_replay_lane(self) -> "ReplaySourceBundleConfig":
        if self.schema_version != REPLAY_SOURCE_BUNDLE_VERSION:
            raise ValueError("replay source bundle schema is unsupported")
        if self.event is None and self.vehicle is None:
            raise ValueError("replay source bundle enables no provider lane")
        return self


class EventReplayArtifact(_ReplayContract):
    """Exact provider responses keyed by the manifest query identity."""

    schema_version: str = EVENT_REPLAY_ARTIFACT_VERSION
    binding: ArtifactBinding
    manifest_id: str = Field(min_length=1)
    responses: dict[str, EventSearchResponse] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _artifact_shape_is_canonical(self) -> "EventReplayArtifact":
        if self.schema_version != EVENT_REPLAY_ARTIFACT_VERSION:
            raise ValueError("event replay artifact schema is unsupported")
        if any(not key or key != key.strip() for key in self.responses):
            raise ValueError("event replay response keys must be exact query IDs")
        return self


class EventReplaySourceArtifact(_ReplayContract):
    """Client-bound event responses whose semantic query IDs can be rebound."""

    schema_version: str = EVENT_REPLAY_SOURCE_VERSION
    client_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    client_name: str = Field(min_length=1)
    source_data_as_of: datetime
    responses: dict[str, EventSearchResponse] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _source_shape_is_canonical(self) -> "EventReplaySourceArtifact":
        if self.schema_version != EVENT_REPLAY_SOURCE_VERSION:
            raise ValueError("event replay source schema is unsupported")
        if self.source_data_as_of.tzinfo is None \
                or self.source_data_as_of.utcoffset() is None:
            raise ValueError("event replay source as-of must be timezone-aware")
        if any(not key or key != key.strip() for key in self.responses):
            raise ValueError(
                "event replay source response keys must be exact query IDs"
            )
        return self


class EventReplayProvider:
    """Callable view over a fully validated event response artifact."""

    def __init__(
        self,
        manifest: EventQueryManifest,
        artifact: EventReplayArtifact,
    ) -> None:
        self._queries = {row.query_id: row for row in manifest.queries}
        self._responses = dict(artifact.responses)

    def __call__(self, query: EventQuerySpec) -> EventSearchResponse:
        expected = self._queries.get(query.query_id)
        if expected is None or expected != query:
            raise ReplayProviderError(
                "event replay received a query outside its bound manifest"
            )
        response = self._responses.get(query.query_id)
        if response is not None:
            return response
        return EventSearchResponse(
            state=CoverageState.NOT_RUN,
            public_detail=(
                "The bound event replay artifact had no response for this "
                "required query; no zero-result claim was made."
            ),
        )


def _rows(
    results: Mapping[str, object],
    lane: str,
) -> Optional[tuple[Mapping[str, object], ...]]:
    raw = results.get(lane)
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ReplayProviderError(f"results.{lane} must be a list")
    rows: list[Mapping[str, object]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ReplayProviderError(
                f"results.{lane}[{index}] must be an object"
            )
        rows.append(value)
    return tuple(rows)


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _literal_match(needle: str, values: tuple[object, ...]) -> bool:
    target = _normalized(needle)
    return bool(target) and any(target in _normalized(value) for value in values)


def _safe_https_url(value: object, *, hosts: frozenset[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https":
        return None
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in hosts):
        return None
    return value


def _sam_url(row: Mapping[str, object], record_id: str) -> str:
    supplied = _safe_https_url(
        row.get("api_url") or row.get("url"),
        hosts=frozenset({"sam.gov"}),
    )
    return supplied or f"https://sam.gov/opp/{quote(record_id, safe='')}/view"


def _usaspending_url(row: Mapping[str, object], record_id: str) -> str:
    supplied = _safe_https_url(
        row.get("url"),
        hosts=frozenset({"usaspending.gov"}),
    )
    return supplied or (
        "https://www.usaspending.gov/award/"
        f"{quote(record_id, safe='_-')}"
    )


def _source_moment(
    row: Mapping[str, object],
    *,
    fallback: datetime,
    label: str,
) -> datetime:
    raw = row.get("retrieved_at")
    if raw in (None, ""):
        return fallback
    return _aware_datetime(raw, label=f"{label}.retrieved_at")


def _sam_values(row: Mapping[str, object]) -> tuple[object, ...]:
    raw = row.get("raw_payload")
    raw = raw if isinstance(raw, dict) else {}
    return tuple(
        row.get(key)
        for key in (
            "title",
            "agency",
            "naics_code",
            "psc_code",
            "description",
            "description_snippet",
            "notice_type",
            "solicitation",
            "office",
        )
    ) + tuple(
        raw.get(key)
        for key in (
            "title",
            "agency",
            "naicsCode",
            "classificationCode",
            "description",
            "description_snippet",
            "type",
            "solicitation",
            "office",
        )
    )


def _award_values(row: Mapping[str, object]) -> tuple[object, ...]:
    place = row.get("place_of_performance")
    place_values = tuple(place.values()) if isinstance(place, dict) else ()
    return tuple(
        row.get(key)
        for key in (
            "award_id",
            "recipient",
            "description",
            "award_type",
            "naics",
            "naics_description",
            "psc",
            "psc_description",
            "set_aside",
            "competition",
            "award_structure",
            "solicitation_id",
            "parent_award_id",
            "parent_generated_id",
            "parent_vehicle_type",
            "parent_award_structure",
            "awarding_agency",
            "agency",
        )
    ) + place_values


def _text_query_matches(
    query: VehicleQuerySpec,
    *,
    values: tuple[object, ...],
    naics: object = None,
    psc: object = None,
    agency: object = None,
) -> bool:
    target = query.target_value or ""
    if query.target_kind is VehicleTargetKind.NAICS:
        return _normalized(naics) == _normalized(target)
    if query.target_kind is VehicleTargetKind.PSC:
        return _normalized(psc) == _normalized(target)
    if query.target_kind is VehicleTargetKind.AGENCY:
        return _literal_match(target, (agency,))
    if query.target_kind is VehicleTargetKind.CLIENT_TERM:
        return _literal_match(target, values)
    return False


def _lead_id(query: VehicleQuerySpec, *identities: str) -> str:
    return "vehicle-replay:" + _stable_sha256({
        "query_id": query.query_id,
        "identities": identities,
    })[:40]


class VehicleSweepReplayProvider:
    """Deterministic local matcher over sanctioned structured sweep rows."""

    def __init__(
        self,
        manifest: VehicleQueryManifest,
        sweep: Mapping[str, object],
    ) -> None:
        results = sweep.get("results")
        if not isinstance(results, dict):
            raise ReplayProviderError("replay sweep requires object-valued results")
        self._manifest = manifest
        self._queries = {row.query_id: row for row in manifest.queries}
        self._sweep_generated_at = _aware_datetime(
            sweep.get("generated_at"),
            label="replay sweep generated_at",
        )
        if self._sweep_generated_at > manifest.as_of:
            raise ReplayProviderError("replay sweep generation postdates manifest")
        self._sam = _rows(results, "sam.gov")
        self._awards = _rows(results, "award_repulls")
        census = results.get("sam_census")
        if census is not None and not isinstance(census, dict):
            raise ReplayProviderError("results.sam_census must be an object")
        self._sam_census = census
        self._validate_rows()

    def _validate_rows(self) -> None:
        for index, row in enumerate(self._sam or ()):
            source = _exact_text(
                row.get("source"),
                label=f"results.sam.gov[{index}].source",
            )
            if source.casefold() != "sam.gov":
                raise ReplayProviderError("SAM replay row has another source")
            _exact_text(
                row.get("source_id"),
                label=f"results.sam.gov[{index}].source_id",
            )
            moment = _source_moment(
                row,
                fallback=self._sweep_generated_at,
                label=f"results.sam.gov[{index}]",
            )
            if moment > self._manifest.as_of:
                raise ReplayProviderError("SAM replay row postdates manifest")
        for index, row in enumerate(self._awards or ()):
            if row.get("kind") not in (None, "award_repull"):
                raise ReplayProviderError("award replay row has another kind")
            if _normalized(row.get("source_system")) not in {
                "usaspending",
                "usaspending.gov",
            }:
                raise ReplayProviderError(
                    "award replay row has another source system"
                )
            _exact_text(
                row.get("generated_id"),
                label=f"results.award_repulls[{index}].generated_id",
            )
            moment = _source_moment(
                row,
                fallback=self._sweep_generated_at,
                label=f"results.award_repulls[{index}]",
            )
            if moment > self._manifest.as_of:
                raise ReplayProviderError("award replay row postdates manifest")

    def __call__(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        expected = self._queries.get(query.query_id)
        if expected is None or expected != query:
            raise ReplayProviderError(
                "vehicle replay received a query outside its bound manifest"
            )
        if query.lane not in _SUPPORTED_VEHICLE_LANES:
            return VehicleSearchResponse(
                state=CoverageState.NOT_RUN,
                public_detail=(
                    "The configured sweep replay has no authorized source "
                    f"for the {query.lane.value} lane; no zero-result claim "
                    "was made."
                ),
            )
        if query.lane is VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES:
            return self._sam_response(query)
        if query.lane is VehicleWatchLane.USASPENDING_IDV:
            return self._idv_response(query)
        return self._order_response(query)

    def _sam_response(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        if self._sam is None:
            return VehicleSearchResponse(
                state=CoverageState.NOT_RUN,
                public_detail=(
                    "The current sweep has no SAM result lane; no zero-result "
                    "claim was made."
                ),
            )
        leads: list[VehicleLead] = []
        for index, row in enumerate(self._sam):
            record_id = str(row["source_id"])
            identity = SourceIdentity(
                source_system="sam.gov",
                record_id=record_id,
            )
            if query.target_kind is VehicleTargetKind.CANDIDATE_SOURCE:
                target = query.target_source_identity
                matches = (
                    target is not None
                    and identity.canonical_key == target.canonical_key
                )
            elif query.target_kind is VehicleTargetKind.KNOWN_PARENT_IDV:
                matches = False
            else:
                raw = row.get("raw_payload")
                raw = raw if isinstance(raw, dict) else {}
                matches = _text_query_matches(
                    query,
                    values=_sam_values(row),
                    naics=row.get("naics_code") or raw.get("naicsCode"),
                    psc=row.get("psc_code") or raw.get("classificationCode"),
                    agency=row.get("agency") or raw.get("agency"),
                )
            if not matches:
                continue
            discovered_at = _source_moment(
                row,
                fallback=self._sweep_generated_at,
                label=f"results.sam.gov[{index}]",
            )
            leads.append(VehicleLead(
                lead_id=_lead_id(query, identity.canonical_key),
                client_id=self._manifest.binding.client_id,
                run_id=self._manifest.binding.run_id,
                scope_sha256=self._manifest.binding.scope_sha256,
                query_id=query.query_id,
                lane=query.lane,
                discovered_at=discovered_at,
                title=str(row.get("title") or f"SAM notice {record_id}"),
                source_url=_sam_url(row, record_id),
                source_identity=identity,
            ))
        state = CoverageState.RETURNED
        detail = (
            "The complete current SAM sweep snapshot was matched locally; "
            "all returned rows remain unverified research leads."
        )
        if self._sam_census is None or self._sam_census.get("complete") is not True:
            state = CoverageState.PARTIAL
            detail = (
                "The current SAM sweep snapshot was matched locally, but its "
                "source census was not marked complete."
            )
        elif int(self._sam_census.get("stale_cache") or 0) > 0:
            state = CoverageState.STALE_SNAPSHOT
            detail = (
                "The current SAM sweep snapshot was matched locally and "
                "reported use of stale source cache rows."
            )
        return VehicleSearchResponse(
            state=state,
            records_returned=len(leads),
            leads=tuple(leads),
            public_detail=detail,
        )

    def _award_lane_unavailable(self) -> Optional[VehicleSearchResponse]:
        if not self._awards:
            return VehicleSearchResponse(
                state=CoverageState.NOT_RUN,
                public_detail=(
                    "The current sweep contains no authorized USAspending "
                    "award re-pull rows; no zero-result claim was made."
                ),
            )
        return None

    def _idv_response(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        unavailable = self._award_lane_unavailable()
        if unavailable is not None:
            return unavailable
        by_parent: dict[str, VehicleLead] = {}
        for index, row in enumerate(self._awards or ()):
            parent_id = row.get("parent_generated_id")
            if not isinstance(parent_id, str) or not parent_id:
                continue
            parent = SourceIdentity(
                source_system="usaspending.gov",
                record_id=parent_id,
            )
            if query.target_kind is VehicleTargetKind.KNOWN_PARENT_IDV:
                target = query.target_source_identity
                matches = (
                    target is not None
                    and parent.canonical_key == target.canonical_key
                )
            else:
                matches = _text_query_matches(
                    query,
                    values=_award_values(row),
                    naics=row.get("naics"),
                    psc=row.get("psc"),
                    agency=row.get("awarding_agency") or row.get("agency"),
                )
            if not matches or parent.canonical_key in by_parent:
                continue
            moment = _source_moment(
                row,
                fallback=self._sweep_generated_at,
                label=f"results.award_repulls[{index}]",
            )
            parent_label = row.get("parent_award_id") or parent_id
            by_parent[parent.canonical_key] = VehicleLead(
                lead_id=_lead_id(query, parent.canonical_key),
                client_id=self._manifest.binding.client_id,
                run_id=self._manifest.binding.run_id,
                scope_sha256=self._manifest.binding.scope_sha256,
                query_id=query.query_id,
                lane=query.lane,
                discovered_at=moment,
                title=f"USAspending parent IDV {parent_label}",
                source_url=_usaspending_url({}, parent_id),
                source_identity=parent,
                parent_idv_identity=parent,
            )
        leads = tuple(by_parent[key] for key in sorted(by_parent))
        return VehicleSearchResponse(
            state=CoverageState.PARTIAL,
            records_returned=len(leads),
            leads=leads,
            public_detail=(
                "The current board-cited award re-pull subset was matched "
                "locally for parent IDVs. It is not a query-scoped complete "
                "USAspending census, so no complete or zero-result claim was "
                "made."
            ),
        )

    def _order_response(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        unavailable = self._award_lane_unavailable()
        if unavailable is not None:
            return unavailable
        target = query.target_source_identity
        leads: list[VehicleLead] = []
        for index, row in enumerate(self._awards or ()):
            parent_id = row.get("parent_generated_id")
            if not isinstance(parent_id, str) or not parent_id:
                continue
            parent = SourceIdentity(
                source_system="usaspending.gov",
                record_id=parent_id,
            )
            if target is None or parent.canonical_key != target.canonical_key:
                continue
            order_id = str(row["generated_id"])
            order = SourceIdentity(
                source_system="usaspending.gov",
                record_id=order_id,
            )
            moment = _source_moment(
                row,
                fallback=self._sweep_generated_at,
                label=f"results.award_repulls[{index}]",
            )
            leads.append(VehicleLead(
                lead_id=_lead_id(
                    query,
                    parent.canonical_key,
                    order.canonical_key,
                ),
                client_id=self._manifest.binding.client_id,
                run_id=self._manifest.binding.run_id,
                scope_sha256=self._manifest.binding.scope_sha256,
                query_id=query.query_id,
                lane=query.lane,
                discovered_at=moment,
                title=str(
                    row.get("description")
                    or row.get("award_id")
                    or f"USAspending order {order_id}"
                ),
                source_url=_usaspending_url(row, order_id),
                source_identity=order,
                parent_idv_identity=parent,
                order_identity=order,
            ))
        return VehicleSearchResponse(
            state=CoverageState.PARTIAL,
            records_returned=len(leads),
            leads=tuple(leads),
            public_detail=(
                "The current board-cited award re-pull subset was matched "
                "locally for order lineage. It is not a query-scoped complete "
                "USAspending census, so no complete or zero-result claim was "
                "made."
            ),
        )


@dataclass(frozen=True)
class ReplayProviderRuntime:
    bindings: ResearchProviderBindings
    event_searcher: Optional[EventReplayProvider]
    vehicle_searcher: Optional[VehicleSweepReplayProvider]
    config_sha256: str


def _unavailable_binding(lane: str) -> ProviderBinding:
    return ProviderBinding(
        adapter_name=f"unavailable-{lane}-provider",
        adapter_version="1",
        config_sha256=_sha256(
            f"candidate-review-v1:no-{lane}-provider".encode("utf-8")
        ),
        mode=ProviderMode.UNAVAILABLE,
    )


def _read_exact(path: Path, expected_sha256: str, *, label: str) -> bytes:
    payload = path.read_bytes()
    observed = _sha256(payload)
    if observed != expected_sha256:
        raise ReplayProviderError(
            f"{label} SHA-256 differs from configured bytes"
        )
    return payload


def load_replay_provider_runtime(
    config_path: Path | str,
    *,
    binding: ArtifactBinding,
    event_manifest: EventQueryManifest,
    vehicle_manifest: VehicleQueryManifest,
    sweep_path: Path | str,
) -> ReplayProviderRuntime:
    """Load exact offline adapters and derive basis-bearing provider hashes."""

    path = Path(config_path).resolve()
    config_bytes = path.read_bytes()
    config_sha256 = _sha256(config_bytes)
    config = ReplayProviderConfig.model_validate(_decode_strict_json(
        config_bytes,
        label="replay provider config",
    ))
    if config.binding != binding:
        raise ReplayProviderError("replay config belongs to another client or run")
    if config.event_manifest_id != event_manifest.manifest_id:
        raise ReplayProviderError("replay config event manifest does not match")
    if config.vehicle_manifest_id != vehicle_manifest.manifest_id:
        raise ReplayProviderError("replay config vehicle manifest does not match")

    event_searcher: Optional[EventReplayProvider] = None
    event_binding = _unavailable_binding("event")
    if config.event is not None:
        event_path = Path(config.event.path)
        if not event_path.is_absolute():
            event_path = path.parent / event_path
        event_bytes = _read_exact(
            event_path.resolve(),
            config.event.sha256,
            label="event replay artifact",
        )
        artifact = EventReplayArtifact.model_validate(_decode_strict_json(
            event_bytes,
            label="event replay artifact",
        ))
        if artifact.binding != binding:
            raise ReplayProviderError("event replay belongs to another client or run")
        if artifact.manifest_id != event_manifest.manifest_id:
            raise ReplayProviderError("event replay belongs to another manifest")
        queries = {row.query_id: row for row in event_manifest.queries}
        required_ids = {row.query_id for row in event_manifest.queries if row.required}
        extra = set(artifact.responses) - required_ids
        if extra:
            raise ReplayProviderError(
                "event replay contains nonrequired or unknown query responses"
            )
        lead_ids: list[str] = []
        for query_id, response in artifact.responses.items():
            query = queries[query_id]
            for lead in response.leads:
                if lead.query_id != query_id or lead.source_class is not query.source_class:
                    raise ReplayProviderError(
                        "event replay lead differs from its manifest query"
                    )
                if lead.discovered_at > vehicle_manifest.as_of:
                    raise ReplayProviderError(
                        "event replay lead postdates the bound research as-of"
                    )
                lead_ids.append(lead.lead_id)
        if len(lead_ids) != len(set(lead_ids)):
            raise ReplayProviderError("event replay lead IDs must be globally unique")
        event_searcher = EventReplayProvider(event_manifest, artifact)
        event_binding = ProviderBinding(
            adapter_name="candidate-review-event-replay",
            adapter_version=REPLAY_ADAPTER_VERSION,
            config_sha256=_stable_sha256({
                "config_sha256": config_sha256,
                "manifest_id": event_manifest.manifest_id,
                "artifact_sha256": config.event.sha256,
            }),
            mode=ProviderMode.REPLAY,
        )

    vehicle_searcher: Optional[VehicleSweepReplayProvider] = None
    vehicle_binding = _unavailable_binding("vehicle")
    if config.vehicle is not None:
        sweep_bytes = _read_exact(
            Path(sweep_path),
            config.vehicle.sweep_sha256,
            label="current replay sweep",
        )
        if config.vehicle.sweep_sha256 != binding.evidence_snapshot_sha256:
            raise ReplayProviderError(
                "vehicle replay sweep differs from the bound evidence snapshot"
            )
        sweep = _decode_strict_json(
            sweep_bytes,
            label="current replay sweep",
        )
        if not isinstance(sweep, dict):
            raise ReplayProviderError("vehicle replay sweep must be an object")
        if sweep.get("client") != binding.client_name:
            raise ReplayProviderError("vehicle replay sweep belongs to another client")
        vehicle_searcher = VehicleSweepReplayProvider(vehicle_manifest, sweep)
        vehicle_binding = ProviderBinding(
            adapter_name="candidate-review-sweep-replay",
            adapter_version=REPLAY_ADAPTER_VERSION,
            config_sha256=_stable_sha256({
                "config_sha256": config_sha256,
                "manifest_id": vehicle_manifest.manifest_id,
                "sweep_sha256": config.vehicle.sweep_sha256,
            }),
            mode=ProviderMode.REPLAY,
        )

    return ReplayProviderRuntime(
        bindings=ResearchProviderBindings(
            event=event_binding,
            vehicle=vehicle_binding,
        ),
        event_searcher=event_searcher,
        vehicle_searcher=vehicle_searcher,
        config_sha256=config_sha256,
    )


def load_replay_source_bundle_runtime(
    bundle_path: Path | str,
    *,
    binding: ArtifactBinding,
    event_manifest: EventQueryManifest,
    vehicle_manifest: VehicleQueryManifest,
    sweep_path: Path | str,
) -> ReplayProviderRuntime:
    """Bind immutable client replay sources to the exact post-re-pull run.

    Event query IDs are derived from semantic query fields, so only responses
    whose IDs exist in the current required manifest are selected.  Missing
    current responses remain ``NOT_RUN`` through ``EventReplayProvider``;
    stale/partial/failed states are preserved.  Vehicle replay is built from
    the current sweep bytes read here, and those bytes must equal the evidence
    digest already carried by ``binding``.
    """

    path = Path(bundle_path).resolve()
    bundle_bytes = path.read_bytes()
    bundle_sha256 = _sha256(bundle_bytes)
    bundle = ReplaySourceBundleConfig.model_validate(_decode_strict_json(
        bundle_bytes,
        label="replay source bundle",
    ))
    if bundle.client_id != binding.client_id \
            or bundle.client_name != binding.client_name:
        raise ReplayProviderError(
            "replay source bundle belongs to another client"
        )

    event_searcher: Optional[EventReplayProvider] = None
    event_binding = _unavailable_binding("event")
    if bundle.event is not None:
        event_path = Path(bundle.event.path)
        if not event_path.is_absolute():
            event_path = path.parent / event_path
        event_bytes = _read_exact(
            event_path.resolve(),
            bundle.event.sha256,
            label="event replay source artifact",
        )
        source = EventReplaySourceArtifact.model_validate(_decode_strict_json(
            event_bytes,
            label="event replay source artifact",
        ))
        if source.client_id != binding.client_id \
                or source.client_name != binding.client_name:
            raise ReplayProviderError(
                "event replay source belongs to another client"
            )
        if source.source_data_as_of > vehicle_manifest.as_of:
            raise ReplayProviderError(
                "event replay source postdates the bound research as-of"
            )
        queries = {row.query_id: row for row in event_manifest.queries}
        required_ids = {
            row.query_id for row in event_manifest.queries if row.required
        }
        selected = {
            query_id: response
            for query_id, response in source.responses.items()
            if query_id in required_ids
        }
        lead_ids: list[str] = []
        for query_id, response in selected.items():
            query = queries[query_id]
            for lead in response.leads:
                if lead.query_id != query_id \
                        or lead.source_class is not query.source_class:
                    raise ReplayProviderError(
                        "event replay source lead differs from its current "
                        "manifest query"
                    )
                if lead.discovered_at > source.source_data_as_of:
                    raise ReplayProviderError(
                        "event replay source lead postdates its source as-of"
                    )
                lead_ids.append(lead.lead_id)
        if len(lead_ids) != len(set(lead_ids)):
            raise ReplayProviderError(
                "event replay source lead IDs must be globally unique"
            )
        rebound = EventReplayArtifact(
            binding=binding,
            manifest_id=event_manifest.manifest_id,
            responses=selected,
        )
        rebound_sha256 = _stable_sha256(
            rebound.model_dump(mode="json")
        )
        event_searcher = EventReplayProvider(event_manifest, rebound)
        event_binding = ProviderBinding(
            adapter_name="candidate-review-event-source-replay",
            adapter_version=REPLAY_ADAPTER_VERSION,
            config_sha256=_stable_sha256({
                "bundle_sha256": bundle_sha256,
                "source_artifact_sha256": bundle.event.sha256,
                "source_data_as_of": source.source_data_as_of.isoformat(),
                "manifest_id": event_manifest.manifest_id,
                "rebound_artifact_sha256": rebound_sha256,
            }),
            mode=ProviderMode.REPLAY,
        )

    vehicle_searcher: Optional[VehicleSweepReplayProvider] = None
    vehicle_binding = _unavailable_binding("vehicle")
    if bundle.vehicle is not None:
        current_sweep_path = Path(sweep_path)
        sweep_bytes = current_sweep_path.read_bytes()
        sweep_sha256 = _sha256(sweep_bytes)
        if sweep_sha256 != binding.evidence_snapshot_sha256:
            raise ReplayProviderError(
                "current vehicle replay sweep differs from the bound "
                "evidence snapshot"
            )
        sweep = _decode_strict_json(
            sweep_bytes,
            label="current replay sweep",
        )
        if not isinstance(sweep, dict):
            raise ReplayProviderError("vehicle replay sweep must be an object")
        if sweep.get("client") != binding.client_name:
            raise ReplayProviderError(
                "vehicle replay sweep belongs to another client"
            )
        vehicle_searcher = VehicleSweepReplayProvider(vehicle_manifest, sweep)
        vehicle_binding = ProviderBinding(
            adapter_name="candidate-review-current-sweep-replay",
            adapter_version=REPLAY_ADAPTER_VERSION,
            config_sha256=_stable_sha256({
                "bundle_sha256": bundle_sha256,
                "manifest_id": vehicle_manifest.manifest_id,
                "sweep_sha256": sweep_sha256,
            }),
            mode=ProviderMode.REPLAY,
        )

    return ReplayProviderRuntime(
        bindings=ResearchProviderBindings(
            event=event_binding,
            vehicle=vehicle_binding,
        ),
        event_searcher=event_searcher,
        vehicle_searcher=vehicle_searcher,
        config_sha256=bundle_sha256,
    )


__all__ = (
    "EVENT_REPLAY_ARTIFACT_VERSION",
    "EVENT_REPLAY_SOURCE_VERSION",
    "REPLAY_ADAPTER_VERSION",
    "REPLAY_PROVIDER_CONFIG_VERSION",
    "REPLAY_SOURCE_BUNDLE_VERSION",
    "CurrentSweepReplay",
    "CurrentSweepSource",
    "EventReplayArtifact",
    "EventReplayProvider",
    "EventReplaySourceArtifact",
    "ReplayFileReference",
    "ReplayProviderConfig",
    "ReplayProviderError",
    "ReplayProviderRuntime",
    "ReplaySourceBundleConfig",
    "VehicleSweepReplayProvider",
    "load_replay_provider_runtime",
    "load_replay_source_bundle_runtime",
)
