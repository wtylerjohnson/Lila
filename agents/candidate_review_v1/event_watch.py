"""Standing event-watch registry contracts for Candidate Review v1.

This module owns a small, curated *search universe*.  It does not claim that
an event is scheduled, open, attended by an agency, sponsored by a vendor, or
otherwise reportable.  Those facts still require authoritative event evidence
through the separate research and verification lanes.

Registry bytes are hashed exactly as stored so an assessment can bind itself
to the precise standing universe used for its search.  Applicability is a pure
tag comparison; priority affects search order only.  Every applicable target
is searched on every run.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Iterable, Mapping, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agents.candidate_review_v1.event_research import (
    normalize_trusted_organizer_domain,
)


EVENT_WATCH_SCHEMA_VERSION = "candidate_review_v1.event_watch_universe.v1"
DEFAULT_EVENT_WATCH_UNIVERSE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "reference"
    / "event_watch_universe.json"
)

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TAG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class _WatchContract(BaseModel):
    """Strict immutable boundary shared by standing-watch models."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class WatchSourceRole(str, Enum):
    """How a source may participate in event research."""

    ORGANIZER = "organizer"
    OPERATOR = "operator"
    DISCOVERY_ONLY = "discovery_only"


class WatchIdentityStatus(str, Enum):
    """Whether an organizer identity and its authority are resolved."""

    VERIFIED = "verified"
    PENDING = "pending"


class WatchTargetKind(str, Enum):
    ORGANIZER = "organizer"
    EVENT_SERIES = "event_series"


class WatchPriority(str, Enum):
    """Search ordering only; never relevance, cadence, or reportability."""

    TIER_1 = "tier_1"
    WATCH = "watch"

    @property
    def cadence(self) -> "WatchCadence":
        """Compatibility projection: every applicable target runs each time."""

        return WatchCadence.EVERY_RUN


class WatchCadence(str, Enum):
    EVERY_RUN = "every_run"


def _canonical_id(value: str, label: str) -> str:
    normalized = value.strip()
    if _ID_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a stable lowercase slug")
    return normalized


def _canonical_aliases(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError("query_aliases must be a sequence of strings")
    normalized: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("query_aliases must contain only strings")
        alias = " ".join(raw.split())
        if not alias:
            raise ValueError("query_aliases cannot contain blank values")
        normalized.append(alias)
    keys = [alias.casefold() for alias in normalized]
    if len(keys) != len(set(keys)):
        raise ValueError("query_aliases must be unique ignoring case")
    return tuple(sorted(normalized, key=lambda value: (value.casefold(), value)))


def _canonical_tag(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not normalized or _TAG_RE.fullmatch(normalized) is None:
        raise ValueError("watch tags must normalize to lowercase slugs")
    return normalized


def _canonical_tags(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError("watch tags must be a sequence of strings")
    normalized: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("watch tags must contain only strings")
        normalized.append(_canonical_tag(raw))
    return tuple(sorted(set(normalized)))


def _canonical_hosts(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError("official_hosts must be a sequence of hostnames")
    normalized: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("official_hosts must contain only strings")
        normalized.append(normalize_trusted_organizer_domain(raw))
    return tuple(sorted(set(normalized)))


class _TaggedWatchRecord(_WatchContract):
    canonical_name: str = Field(min_length=1)
    priority: WatchPriority
    query_aliases: tuple[str, ...] = Field(default_factory=tuple)
    mission_tags: tuple[str, ...] = Field(default_factory=tuple)
    buyer_tags: tuple[str, ...] = Field(default_factory=tuple)
    agency_tags: tuple[str, ...] = Field(default_factory=tuple)
    capability_tags: tuple[str, ...] = Field(default_factory=tuple)
    enabled: bool = True

    @field_validator("query_aliases", mode="before")
    @classmethod
    def _aliases_are_canonical(cls, value: object) -> tuple[str, ...]:
        return _canonical_aliases(value)

    @field_validator(
        "mission_tags",
        "buyer_tags",
        "agency_tags",
        "capability_tags",
        mode="before",
    )
    @classmethod
    def _tags_are_canonical(cls, value: object) -> tuple[str, ...]:
        return _canonical_tags(value)

    @model_validator(mode="after")
    def _name_is_not_repeated_as_alias(self) -> "_TaggedWatchRecord":
        if self.canonical_name.casefold() in {
            alias.casefold() for alias in self.query_aliases
        }:
            raise ValueError("canonical_name cannot be repeated as an alias")
        return self

    @property
    def cadence(self) -> WatchCadence:
        return self.priority.cadence


class WatchOrganizer(_TaggedWatchRecord):
    organizer_id: str
    role: WatchSourceRole
    identity_status: WatchIdentityStatus
    official_hosts: tuple[str, ...] = Field(default_factory=tuple)
    all_client_watch: bool = False
    include_as_target: bool = True

    @field_validator("organizer_id")
    @classmethod
    def _id_is_stable(cls, value: str) -> str:
        return _canonical_id(value, "organizer_id")

    @field_validator("official_hosts", mode="before")
    @classmethod
    def _hosts_are_exact(cls, value: object) -> tuple[str, ...]:
        return _canonical_hosts(value)

    @model_validator(mode="after")
    def _pending_identity_cannot_authorize_hosts(self) -> "WatchOrganizer":
        if (
            self.identity_status is WatchIdentityStatus.PENDING
            and self.official_hosts
        ):
            raise ValueError(
                "pending organizer identities cannot authorize official hosts"
            )
        return self


class WatchEventSeries(_TaggedWatchRecord):
    event_id: str
    organizer_id: str

    @field_validator("event_id")
    @classmethod
    def _event_id_is_stable(cls, value: str) -> str:
        return _canonical_id(value, "event_id")

    @field_validator("organizer_id")
    @classmethod
    def _organizer_id_is_stable(cls, value: str) -> str:
        return _canonical_id(value, "organizer_id")


class EventWatchUniverse(_WatchContract):
    """Curated names and tags, with no claim about a particular event date."""

    schema_version: str
    revision_id: str = Field(min_length=1)
    organizers: tuple[WatchOrganizer, ...]
    event_series: tuple[WatchEventSeries, ...]

    @model_validator(mode="after")
    def _universe_is_resolved_and_unambiguous(self) -> "EventWatchUniverse":
        if self.schema_version != EVENT_WATCH_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {EVENT_WATCH_SCHEMA_VERSION}"
            )
        organizer_ids = [row.organizer_id for row in self.organizers]
        event_ids = [row.event_id for row in self.event_series]
        if len(organizer_ids) != len(set(organizer_ids)):
            raise ValueError("organizer_id values must be unique")
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event_id values must be unique")
        if set(organizer_ids) & set(event_ids):
            raise ValueError("organizer_id and event_id values must be disjoint")
        known_organizers = set(organizer_ids)
        for event in self.event_series:
            if event.organizer_id not in known_organizers:
                raise ValueError(
                    f"event {event.event_id} references an unknown organizer"
                )
        target_rows: tuple[_TaggedWatchRecord, ...] = (
            *(
                organizer
                for organizer in self.organizers
                if organizer.include_as_target
            ),
            *self.event_series,
        )
        self._require_unique_query_names(target_rows, "watch target")
        return self

    @staticmethod
    def _require_unique_query_names(
        rows: Iterable[_TaggedWatchRecord],
        label: str,
    ) -> None:
        owners: dict[str, str] = {}
        for row in rows:
            identifier = (
                row.organizer_id
                if isinstance(row, WatchOrganizer)
                else row.event_id
            )
            for name in (row.canonical_name, *row.query_aliases):
                key = name.casefold()
                if key in owners:
                    raise ValueError(
                        f"{label} query name {name!r} is shared by "
                        f"{owners[key]} and {identifier}"
                    )
                owners[key] = identifier

    @property
    def authoritative_hosts(self) -> tuple[str, ...]:
        """Hosts eligible for organizer authority, never discovery portals."""

        return tuple(sorted({
            host
            for organizer in self.organizers
            if organizer.enabled
            and organizer.identity_status is WatchIdentityStatus.VERIFIED
            and organizer.role in {
                WatchSourceRole.ORGANIZER,
                WatchSourceRole.OPERATOR,
            }
            for host in organizer.official_hosts
        }))


class WatchFrameTags(_WatchContract):
    """Analyst-approved controlled tags used for standing-watch selection."""

    mission_tags: tuple[str, ...] = Field(default_factory=tuple)
    buyer_tags: tuple[str, ...] = Field(default_factory=tuple)
    agency_tags: tuple[str, ...] = Field(default_factory=tuple)
    capability_tags: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator(
        "mission_tags",
        "buyer_tags",
        "agency_tags",
        "capability_tags",
        mode="before",
    )
    @classmethod
    def _tags_are_canonical(cls, value: object) -> tuple[str, ...]:
        return _canonical_tags(value)


class WatchTarget(_WatchContract):
    """Uniform deterministic projection for a selected registry target."""

    id: str
    kind: WatchTargetKind
    canonical_name: str
    priority: WatchPriority
    role: WatchSourceRole
    identity_status: WatchIdentityStatus
    official_hosts: tuple[str, ...]
    query_aliases: tuple[str, ...]
    mission_tags: tuple[str, ...]
    buyer_tags: tuple[str, ...]
    agency_tags: tuple[str, ...]
    capability_tags: tuple[str, ...]
    all_client_watch: bool
    enabled: bool

    @property
    def cadence(self) -> WatchCadence:
        return self.priority.cadence

    @property
    def query_terms(self) -> tuple[str, ...]:
        return (self.canonical_name, *self.query_aliases)


class WatchTargetSelection(_WatchContract):
    applicable: tuple[WatchTarget, ...]
    search_due: tuple[WatchTarget, ...]
    cadence_deferred: tuple[WatchTarget, ...]
    scope_excluded: tuple[WatchTarget, ...]

    @model_validator(mode="after")
    def _selection_is_a_disjoint_partition(self) -> "WatchTargetSelection":
        applicable_ids = {row.id for row in self.applicable}
        due_ids = {row.id for row in self.search_due}
        deferred_ids = {row.id for row in self.cadence_deferred}
        excluded_ids = {row.id for row in self.scope_excluded}
        if applicable_ids & excluded_ids:
            raise ValueError("watch target selection must be disjoint")
        if deferred_ids:
            raise ValueError(
                "applicable standing-watch targets cannot be cadence deferred"
            )
        if due_ids != applicable_ids:
            raise ValueError(
                "every applicable standing-watch target must be search due"
            )
        for label, rows in (
            ("applicable", self.applicable),
            ("search_due", self.search_due),
            ("cadence_deferred", self.cadence_deferred),
            ("scope_excluded", self.scope_excluded),
        ):
            keys = [(row.kind.value, row.id) for row in rows]
            if keys != sorted(keys) or len(keys) != len(set(keys)):
                raise ValueError(
                    f"{label} watch targets must be unique and ordered"
                )
        return self


def _registry_path(path: Optional[Path | str]) -> Path:
    if path is None:
        return DEFAULT_EVENT_WATCH_UNIVERSE_PATH
    return Path(path)


def watch_universe_sha256(path: Optional[Path | str] = None) -> str:
    """Return SHA-256 over the registry's exact bytes, including whitespace."""

    return hashlib.sha256(_registry_path(path).read_bytes()).hexdigest()


def load_event_watch_universe(
    path: Optional[Path | str] = None,
) -> EventWatchUniverse:
    """Load and strictly validate the standing universe without side effects."""

    payload = _registry_path(path).read_bytes()
    return EventWatchUniverse.model_validate_json(payload)


def _target_tags(target: WatchTarget) -> tuple[set[str], ...]:
    return (
        set(target.mission_tags),
        set(target.buyer_tags),
        set(target.agency_tags),
        set(target.capability_tags),
    )


def _frame_tag_sets(frame_tags: WatchFrameTags) -> tuple[set[str], ...]:
    return (
        set(frame_tags.mission_tags),
        set(frame_tags.buyer_tags),
        set(frame_tags.agency_tags),
        set(frame_tags.capability_tags),
    )


def _watch_targets(universe: EventWatchUniverse) -> tuple[WatchTarget, ...]:
    organizers = {row.organizer_id: row for row in universe.organizers}
    targets: list[WatchTarget] = []
    for organizer in universe.organizers:
        if not organizer.include_as_target:
            continue
        targets.append(WatchTarget(
            id=organizer.organizer_id,
            kind=WatchTargetKind.ORGANIZER,
            canonical_name=organizer.canonical_name,
            priority=organizer.priority,
            role=organizer.role,
            identity_status=organizer.identity_status,
            official_hosts=organizer.official_hosts,
            query_aliases=organizer.query_aliases,
            mission_tags=organizer.mission_tags,
            buyer_tags=organizer.buyer_tags,
            agency_tags=organizer.agency_tags,
            capability_tags=organizer.capability_tags,
            all_client_watch=organizer.all_client_watch,
            enabled=organizer.enabled,
        ))
    for event in universe.event_series:
        organizer = organizers[event.organizer_id]
        targets.append(WatchTarget(
            id=event.event_id,
            kind=WatchTargetKind.EVENT_SERIES,
            canonical_name=event.canonical_name,
            priority=event.priority,
            role=organizer.role,
            identity_status=organizer.identity_status,
            official_hosts=organizer.official_hosts,
            query_aliases=event.query_aliases,
            mission_tags=event.mission_tags,
            buyer_tags=event.buyer_tags,
            agency_tags=event.agency_tags,
            capability_tags=event.capability_tags,
            all_client_watch=False,
            enabled=event.enabled and organizer.enabled,
        ))
    return tuple(sorted(targets, key=lambda row: (row.kind.value, row.id)))


def select_watch_targets(
    universe: EventWatchUniverse,
    frame_tags: WatchFrameTags,
    *,
    as_of: datetime,
    last_checked_at: Mapping[str, datetime],
) -> WatchTargetSelection:
    """Partition targets by applicability for an every-run search census.

    The ten policy organizer sources are applicable to every client.  Event
    series and all other organizers require same-dimension tag overlap.  Every
    applicable target is due on every run.  Priority is retained only so the
    query manifest can search Tier 1 targets first when a provider is bounded;
    it never suppresses a required coverage row.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("event watch as_of must be timezone-aware")
    targets = _watch_targets(universe)
    target_ids = {target.id for target in targets}
    unknown_checks = set(last_checked_at) - target_ids
    if unknown_checks:
        raise ValueError("last_checked_at contains an unknown watch target")
    for target_id, checked_at in last_checked_at.items():
        if not isinstance(checked_at, datetime) or (
            checked_at.tzinfo is None or checked_at.utcoffset() is None
        ):
            raise ValueError(
                f"last_checked_at for {target_id} must be timezone-aware"
            )
        if checked_at > as_of:
            raise ValueError("last_checked_at cannot be after as_of")

    frame_sets = _frame_tag_sets(frame_tags)
    applicable: list[WatchTarget] = []
    search_due: list[WatchTarget] = []
    scope_excluded: list[WatchTarget] = []
    for target in targets:
        matches = any(
            target_values & frame_values
            for target_values, frame_values in zip(
                _target_tags(target),
                frame_sets,
            )
        )
        in_scope = target.enabled and (target.all_client_watch or matches)
        if in_scope:
            applicable.append(target)
            search_due.append(target)
        else:
            scope_excluded.append(target)
    return WatchTargetSelection(
        applicable=tuple(applicable),
        search_due=tuple(search_due),
        cadence_deferred=(),
        scope_excluded=tuple(scope_excluded),
    )
