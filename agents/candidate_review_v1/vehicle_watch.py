"""Deterministic vehicle and IDIQ discovery for Candidate Review v1.

This module is intentionally a collection boundary, not a publication
boundary.  It turns an analyst-approved, client-bound frame into a complete
query census and records unverified leads from an injected provider.  It does
not call the network, invoke a model, consult the legacy vehicle catalog, or
promote a lead into :class:`VehicleSignal`.

Vehicle names, parent IDVs, and orders remain separate identities throughout
collection.  A later verification stage must fetch official evidence and use
the existing ``VehicleSignal`` validation contract before anything is shown in
the report.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import hashlib
import json
import re
from typing import Callable, Iterable, Literal, Mapping, Optional, Protocol, Union
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CoverageRecord,
    CoverageState,
    EvidenceKind,
    EvidenceRecord,
    SourceIdentity,
)


VEHICLE_WATCH_SCHEMA_VERSION = "candidate_review_v1.vehicle_watch.v2"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_DETAIL_RE = re.compile(
    r"(?:traceback|api[_ -]?key|authorization:|bearer\s+[a-z0-9._-]+)",
    re.IGNORECASE,
)
_MAX_FRAME_VALUES = 128
_MAX_CANDIDATE_SOURCE_IDENTITIES = 1024


class _VehicleWatchContract(BaseModel):
    """Strict immutable boundary shared by vehicle-watch contracts."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_sha256(value: str, label: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_public_detail(value: str) -> None:
    if _SAFE_DETAIL_RE.search(value):
        raise ValueError("public detail contains sensitive provider internals")


def _require_unique(values: Iterable[str], label: str) -> None:
    rows = tuple(values)
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} must be unique")


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _canonical_text_values(
    values: tuple[str, ...],
    label: str,
) -> tuple[str, ...]:
    if len(values) > _MAX_FRAME_VALUES:
        raise ValueError(
            f"{label} cannot contain more than {_MAX_FRAME_VALUES} values"
        )
    rows = tuple(_normalized_text(value) for value in values)
    if any(not value for value in rows):
        raise ValueError(f"{label} cannot contain blank values")
    _require_unique((value.casefold() for value in rows), label)
    return tuple(sorted(rows, key=lambda value: (value.casefold(), value)))


def _identity_payload(identity: SourceIdentity) -> dict[str, Optional[str]]:
    return {
        "source_system": identity.source_system,
        "record_id": identity.record_id,
        "revision_id": identity.revision_id,
    }


def _identity_label(identity: SourceIdentity) -> str:
    revision = f" revision {identity.revision_id}" \
        if identity.revision_id else ""
    return (
        f"{identity.source_system} exact record {identity.record_id}"
        f"{revision}"
    )


def _canonical_identities(
    values: tuple[SourceIdentity, ...],
    label: str,
    *,
    maximum: int = _MAX_FRAME_VALUES,
) -> tuple[SourceIdentity, ...]:
    if len(values) > maximum:
        raise ValueError(
            f"{label} cannot contain more than {maximum} values"
        )
    _require_unique((value.canonical_key for value in values), label)
    return tuple(sorted(values, key=lambda value: value.canonical_key))


def _digest(payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class VehicleWatchLane(str, Enum):
    """Authorized public and operator-supplied collection lanes."""

    SAM_CONTRACT_OPPORTUNITIES = "sam_contract_opportunities"
    USASPENDING_IDV = "usaspending_idv"
    USASPENDING_ORDER_LINEAGE = "usaspending_order_lineage"
    GSA_PROGRAM_RECORDS = "gsa_elibrary_program_records"
    OFFICIAL_AGENCY_PROGRAM_FORECAST = \
        "official_agency_program_forecast"
    AUTHORIZED_RESTRICTED_EXPORT = "authorized_restricted_export"


PUBLIC_VEHICLE_WATCH_LANES = (
    VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
    VehicleWatchLane.USASPENDING_IDV,
    VehicleWatchLane.USASPENDING_ORDER_LINEAGE,
    VehicleWatchLane.GSA_PROGRAM_RECORDS,
    VehicleWatchLane.OFFICIAL_AGENCY_PROGRAM_FORECAST,
)


class VehicleTargetKind(str, Enum):
    CLIENT_TERM = "client_term"
    AGENCY = "agency"
    NAICS = "naics"
    PSC = "psc"
    CANDIDATE_SOURCE = "candidate_source"
    KNOWN_PARENT_IDV = "known_parent_idv"
    RESTRICTED_SNAPSHOT = "restricted_snapshot"
    EMPTY_LANE = "empty_lane"


_LANE_TARGET_KINDS = {
    VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES: (
        VehicleTargetKind.CLIENT_TERM,
        VehicleTargetKind.AGENCY,
        VehicleTargetKind.NAICS,
        VehicleTargetKind.PSC,
        VehicleTargetKind.CANDIDATE_SOURCE,
        VehicleTargetKind.KNOWN_PARENT_IDV,
    ),
    VehicleWatchLane.USASPENDING_IDV: (
        VehicleTargetKind.CLIENT_TERM,
        VehicleTargetKind.AGENCY,
        VehicleTargetKind.NAICS,
        VehicleTargetKind.PSC,
        VehicleTargetKind.KNOWN_PARENT_IDV,
    ),
    VehicleWatchLane.USASPENDING_ORDER_LINEAGE: (
        VehicleTargetKind.KNOWN_PARENT_IDV,
    ),
    VehicleWatchLane.GSA_PROGRAM_RECORDS: (
        VehicleTargetKind.CLIENT_TERM,
        VehicleTargetKind.AGENCY,
        VehicleTargetKind.NAICS,
        VehicleTargetKind.PSC,
        VehicleTargetKind.KNOWN_PARENT_IDV,
    ),
    VehicleWatchLane.OFFICIAL_AGENCY_PROGRAM_FORECAST: (
        VehicleTargetKind.CLIENT_TERM,
        VehicleTargetKind.AGENCY,
        VehicleTargetKind.NAICS,
        VehicleTargetKind.PSC,
        VehicleTargetKind.CANDIDATE_SOURCE,
    ),
}


_LANE_INSTRUCTIONS = {
    VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES: (
        "Search SAM.gov Contract Opportunities for an exact vehicle, IDIQ, "
        "on-ramp, pool, ordering, or task-order record"
    ),
    VehicleWatchLane.USASPENDING_IDV: (
        "Search USAspending authoritative IDV records for exact parent award "
        "identity and agency ownership"
    ),
    VehicleWatchLane.USASPENDING_ORDER_LINEAGE: (
        "Search USAspending order lineage under the exact supplied parent IDV"
    ),
    VehicleWatchLane.GSA_PROGRAM_RECORDS: (
        "Search official GSA eLibrary and program records for exact vehicle "
        "identity, scope, and access facts"
    ),
    VehicleWatchLane.OFFICIAL_AGENCY_PROGRAM_FORECAST: (
        "Search official agency program and acquisition forecast pages for "
        "vehicle, IDIQ, on-ramp, or ordering facts"
    ),
    VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT: (
        "Inspect the explicitly authorized operator-supplied export snapshot"
    ),
}


class RestrictedVehicleExport(_VehicleWatchContract):
    """Metadata for a supplied restricted snapshot; never credentials."""

    snapshot_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    snapshot_sha256: str
    captured_at: datetime
    fresh_through: date
    authorization_reference: str = Field(min_length=1)
    operator_supplied: Literal[True] = True

    @model_validator(mode="after")
    def _snapshot_is_explicit_and_bounded(self) -> "RestrictedVehicleExport":
        _require_sha256(self.snapshot_sha256, "restricted snapshot SHA-256")
        _require_aware(self.captured_at, "restricted snapshot captured_at")
        if self.fresh_through < self.captured_at.date():
            raise ValueError("restricted snapshot freshness predates capture")
        _require_public_detail(self.authorization_reference)
        return self


class VehicleWatchFrame(_VehicleWatchContract):
    """Exact analyst-approved search scope for one client and run."""

    schema_version: str = VEHICLE_WATCH_SCHEMA_VERSION
    binding: ArtifactBinding
    revision_sha256: str
    approved_at: datetime
    window_start: date
    client_terms: tuple[str, ...] = Field(default_factory=tuple)
    agencies: tuple[str, ...] = Field(default_factory=tuple)
    naics_codes: tuple[str, ...] = Field(default_factory=tuple)
    psc_codes: tuple[str, ...] = Field(default_factory=tuple)
    candidate_source_identities: tuple[SourceIdentity, ...] = Field(
        default_factory=tuple
    )
    known_parent_idvs: tuple[SourceIdentity, ...] = Field(default_factory=tuple)
    restricted_export: Optional[RestrictedVehicleExport] = None

    @model_validator(mode="after")
    def _frame_is_approved_and_exact(self) -> "VehicleWatchFrame":
        if self.schema_version != VEHICLE_WATCH_SCHEMA_VERSION:
            raise ValueError("vehicle watch schema version is unsupported")
        _require_sha256(self.revision_sha256, "vehicle frame revision SHA-256")
        _require_aware(self.approved_at, "vehicle frame approved_at")
        terms = _canonical_text_values(self.client_terms, "client terms")
        agencies = _canonical_text_values(self.agencies, "agencies")
        naics = _canonical_text_values(self.naics_codes, "NAICS codes")
        psc = _canonical_text_values(self.psc_codes, "PSC codes")
        if any(re.fullmatch(r"\d{2,6}", value) is None for value in naics):
            raise ValueError("NAICS codes must contain 2 to 6 digits")
        if any(re.fullmatch(r"[A-Za-z0-9]{4}", value) is None for value in psc):
            raise ValueError("PSC codes must contain exactly 4 alphanumerics")
        candidates = _canonical_identities(
            self.candidate_source_identities,
            "candidate source identities",
            maximum=_MAX_CANDIDATE_SOURCE_IDENTITIES,
        )
        parents = _canonical_identities(
            self.known_parent_idvs,
            "known parent IDV identities",
        )
        overlap = {row.canonical_key for row in candidates} & {
            row.canonical_key for row in parents
        }
        if overlap:
            raise ValueError(
                "candidate source and parent IDV identities must remain separate"
            )
        if not any((terms, agencies, naics, psc, candidates, parents)):
            raise ValueError("vehicle watch frame requires approved search inputs")
        return self

    @property
    def frame_sha256(self) -> str:
        """Canonical digest independent of analyst input ordering."""

        return _digest({
            "schema_version": self.schema_version,
            "binding": self.binding.model_dump(mode="json"),
            "revision_sha256": self.revision_sha256,
            "approved_at": self.approved_at.isoformat(),
            "window_start": self.window_start.isoformat(),
            "client_terms": _canonical_text_values(
                self.client_terms, "client terms"
            ),
            "agencies": _canonical_text_values(self.agencies, "agencies"),
            "naics_codes": _canonical_text_values(
                self.naics_codes, "NAICS codes"
            ),
            "psc_codes": _canonical_text_values(self.psc_codes, "PSC codes"),
            "candidate_source_identities": [
                _identity_payload(row) for row in _canonical_identities(
                    self.candidate_source_identities,
                    "candidate source identities",
                    maximum=_MAX_CANDIDATE_SOURCE_IDENTITIES,
                )
            ],
            "known_parent_idvs": [
                _identity_payload(row) for row in _canonical_identities(
                    self.known_parent_idvs,
                    "known parent IDV identities",
                )
            ],
            "restricted_export": (
                self.restricted_export.model_dump(mode="json")
                if self.restricted_export is not None else None
            ),
        })


def _target_id(
    kind: VehicleTargetKind,
    *,
    value: Optional[str] = None,
    identity: Optional[SourceIdentity] = None,
) -> str:
    payload = {
        "kind": kind.value,
        "value": value,
        "identity": _identity_payload(identity) if identity else None,
    }
    return f"{kind.value}:{_digest(payload)[:24]}"


class VehicleQuerySpec(_VehicleWatchContract):
    query_id: str
    lane: VehicleWatchLane
    target_kind: VehicleTargetKind
    target_id: str = Field(min_length=1)
    target_value: Optional[str] = None
    target_source_identity: Optional[SourceIdentity] = None
    query_text: str = Field(min_length=1)
    required: bool
    restricted_snapshot_id: Optional[str] = None
    restricted_snapshot_sha256: Optional[str] = None
    restricted_fresh_through: Optional[date] = None

    @model_validator(mode="after")
    def _query_identity_is_recomputable(self) -> "VehicleQuerySpec":
        text_target = self.target_kind in {
            VehicleTargetKind.CLIENT_TERM,
            VehicleTargetKind.AGENCY,
            VehicleTargetKind.NAICS,
            VehicleTargetKind.PSC,
            VehicleTargetKind.RESTRICTED_SNAPSHOT,
        }
        identity_target = self.target_kind in {
            VehicleTargetKind.CANDIDATE_SOURCE,
            VehicleTargetKind.KNOWN_PARENT_IDV,
        }
        if text_target != bool(self.target_value):
            raise ValueError("query text target shape differs from target kind")
        if identity_target != bool(self.target_source_identity):
            raise ValueError("query identity target shape differs from target kind")
        if self.target_kind is VehicleTargetKind.EMPTY_LANE:
            if self.required or self.target_value or self.target_source_identity:
                raise ValueError("empty-lane query must be scope excluded")
        if self.lane is VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT:
            if self.target_kind is VehicleTargetKind.RESTRICTED_SNAPSHOT:
                if not self.required:
                    raise ValueError("supplied restricted snapshot must be required")
                if not self.restricted_snapshot_id \
                        or not self.restricted_snapshot_sha256:
                    raise ValueError("restricted query requires exact snapshot")
                if self.restricted_fresh_through is None:
                    raise ValueError(
                        "restricted query requires a freshness boundary"
                    )
                _require_sha256(
                    self.restricted_snapshot_sha256,
                    "restricted query snapshot SHA-256",
                )
            elif self.target_kind is not VehicleTargetKind.EMPTY_LANE:
                raise ValueError("restricted lane uses only snapshot targets")
        elif (
            self.restricted_snapshot_id
            or self.restricted_snapshot_sha256
            or self.restricted_fresh_through
        ):
            raise ValueError("public query cannot carry restricted metadata")
        if self.lane in PUBLIC_VEHICLE_WATCH_LANES \
                and self.target_kind not in {
                    *_LANE_TARGET_KINDS[self.lane],
                    VehicleTargetKind.EMPTY_LANE,
                }:
            raise ValueError("target kind is not valid for this public lane")
        expected_target_id = _target_id(
            self.target_kind,
            value=self.target_value,
            identity=self.target_source_identity,
        )
        if self.target_id != expected_target_id:
            raise ValueError("vehicle query target_id does not match its content")
        expected_query_id = _digest(_query_payload(self, include_query_id=False))
        if self.query_id != expected_query_id:
            raise ValueError("vehicle query_id does not match its content")
        return self


def _query_payload(
    query: VehicleQuerySpec,
    *,
    include_query_id: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": VEHICLE_WATCH_SCHEMA_VERSION,
        "lane": query.lane.value,
        "target_kind": query.target_kind.value,
        "target_id": query.target_id,
        "target_value": query.target_value,
        "target_source_identity": (
            _identity_payload(query.target_source_identity)
            if query.target_source_identity else None
        ),
        "query_text": query.query_text,
        "required": query.required,
        "restricted_snapshot_id": query.restricted_snapshot_id,
        "restricted_snapshot_sha256": query.restricted_snapshot_sha256,
        "restricted_fresh_through": (
            query.restricted_fresh_through.isoformat()
            if query.restricted_fresh_through else None
        ),
    }
    if include_query_id:
        payload["query_id"] = query.query_id
    return payload


def _make_query(
    *,
    lane: VehicleWatchLane,
    kind: VehicleTargetKind,
    value: Optional[str] = None,
    identity: Optional[SourceIdentity] = None,
    required: bool,
    snapshot: Optional[RestrictedVehicleExport] = None,
) -> VehicleQuerySpec:
    target_id = _target_id(kind, value=value, identity=identity)
    if kind is VehicleTargetKind.EMPTY_LANE:
        query_text = (
            f"{_LANE_INSTRUCTIONS[lane]}; no approved target was supplied, "
            "so this lane is scope excluded."
        )
    else:
        target = value if value is not None else _identity_label(identity)
        query_text = f"{_LANE_INSTRUCTIONS[lane]}; target: {target}."
    values: dict[str, object] = {
        "lane": lane,
        "target_kind": kind,
        "target_id": target_id,
        "target_value": value,
        "target_source_identity": identity,
        "query_text": query_text,
        "required": required,
        "restricted_snapshot_id": snapshot.snapshot_id if snapshot else None,
        "restricted_snapshot_sha256": (
            snapshot.snapshot_sha256 if snapshot else None
        ),
        "restricted_fresh_through": (
            snapshot.fresh_through if snapshot else None
        ),
    }
    seed = VehicleQuerySpec.model_construct(query_id="pending", **values)
    return VehicleQuerySpec(
        query_id=_digest(_query_payload(seed, include_query_id=False)),
        **values,
    )


def _targets_for_frame(
    frame: VehicleWatchFrame,
) -> dict[VehicleTargetKind, tuple[Union[str, SourceIdentity], ...]]:
    return {
        VehicleTargetKind.CLIENT_TERM: _canonical_text_values(
            frame.client_terms, "client terms"
        ),
        VehicleTargetKind.AGENCY: _canonical_text_values(
            frame.agencies, "agencies"
        ),
        VehicleTargetKind.NAICS: _canonical_text_values(
            frame.naics_codes, "NAICS codes"
        ),
        VehicleTargetKind.PSC: _canonical_text_values(
            frame.psc_codes, "PSC codes"
        ),
        VehicleTargetKind.CANDIDATE_SOURCE: _canonical_identities(
            frame.candidate_source_identities,
            "candidate source identities",
            maximum=_MAX_CANDIDATE_SOURCE_IDENTITIES,
        ),
        VehicleTargetKind.KNOWN_PARENT_IDV: _canonical_identities(
            frame.known_parent_idvs,
            "known parent IDV identities",
        ),
    }


def _manifest_payload(
    *,
    binding: ArtifactBinding,
    frame_revision_sha256: str,
    frame_sha256: str,
    approved_at: datetime,
    as_of: datetime,
    window_start: date,
    queries: tuple[VehicleQuerySpec, ...],
) -> dict[str, object]:
    return {
        "version": VEHICLE_WATCH_SCHEMA_VERSION,
        "binding": binding.model_dump(mode="json"),
        "frame_revision_sha256": frame_revision_sha256,
        "frame_sha256": frame_sha256,
        "approved_at": approved_at.isoformat(),
        "as_of": as_of.isoformat(),
        "window_start": window_start.isoformat(),
        "queries": [
            _query_payload(query, include_query_id=True) for query in queries
        ],
    }


class VehicleQueryManifest(_VehicleWatchContract):
    manifest_id: str
    version: str = VEHICLE_WATCH_SCHEMA_VERSION
    binding: ArtifactBinding
    frame_revision_sha256: str
    frame_sha256: str
    approved_at: datetime
    as_of: datetime
    window_start: date
    queries: tuple[VehicleQuerySpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _manifest_closes_the_approved_frame(self) -> "VehicleQueryManifest":
        if self.version != VEHICLE_WATCH_SCHEMA_VERSION:
            raise ValueError("vehicle query manifest version is unsupported")
        _require_sha256(self.manifest_id, "vehicle query manifest_id")
        _require_sha256(
            self.frame_revision_sha256,
            "vehicle manifest frame revision SHA-256",
        )
        _require_sha256(self.frame_sha256, "vehicle manifest frame SHA-256")
        _require_aware(self.approved_at, "vehicle manifest approved_at")
        _require_aware(self.as_of, "vehicle manifest as_of")
        if self.approved_at > self.as_of:
            raise ValueError("vehicle frame approval postdates manifest as_of")
        if self.window_start > self.as_of.date():
            raise ValueError("vehicle watch window begins after manifest as_of")
        _require_unique(
            (query.query_id for query in self.queries),
            "vehicle manifest query IDs",
        )
        _require_unique(
            (
                f"{query.lane.value}\x00{query.target_id}"
                for query in self.queries
            ),
            "vehicle lane target pairs",
        )
        observed_lanes = {query.lane for query in self.queries}
        if observed_lanes != set(VehicleWatchLane):
            raise ValueError("vehicle manifest must close every authorized lane")
        if tuple(self.queries) != tuple(sorted(
            self.queries,
            key=lambda row: (
                list(VehicleWatchLane).index(row.lane),
                list(VehicleTargetKind).index(row.target_kind),
                row.target_id,
            ),
        )):
            raise ValueError("vehicle manifest queries are not canonical")
        restricted = tuple(
            query for query in self.queries
            if query.lane is VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT
        )
        if len(restricted) != 1:
            raise ValueError("vehicle manifest requires one restricted-lane row")
        expected = _digest(_manifest_payload(
            binding=self.binding,
            frame_revision_sha256=self.frame_revision_sha256,
            frame_sha256=self.frame_sha256,
            approved_at=self.approved_at,
            as_of=self.as_of,
            window_start=self.window_start,
            queries=self.queries,
        ))
        if self.manifest_id != expected:
            raise ValueError("vehicle manifest_id does not match its content")
        return self


def build_vehicle_query_manifest(
    frame: VehicleWatchFrame,
    as_of: datetime,
) -> VehicleQueryManifest:
    """Build one deterministic, target-addressable collection census."""

    _require_aware(as_of, "vehicle query manifest as_of")
    if frame.approved_at > as_of:
        raise ValueError("vehicle frame approval postdates manifest as_of")
    if frame.window_start > as_of.date():
        raise ValueError("vehicle watch window begins after manifest as_of")
    if frame.restricted_export is not None \
            and frame.restricted_export.captured_at > as_of:
        raise ValueError("restricted snapshot capture postdates manifest as_of")

    targets = _targets_for_frame(frame)
    queries: list[VehicleQuerySpec] = []
    for lane in PUBLIC_VEHICLE_WATCH_LANES:
        lane_rows: list[VehicleQuerySpec] = []
        for kind in _LANE_TARGET_KINDS[lane]:
            for target in targets[kind]:
                lane_rows.append(_make_query(
                    lane=lane,
                    kind=kind,
                    value=target if isinstance(target, str) else None,
                    identity=(target if isinstance(target, SourceIdentity)
                              else None),
                    required=True,
                ))
        if not lane_rows:
            lane_rows.append(_make_query(
                lane=lane,
                kind=VehicleTargetKind.EMPTY_LANE,
                required=False,
            ))
        queries.extend(lane_rows)

    if frame.restricted_export is None:
        queries.append(_make_query(
            lane=VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT,
            kind=VehicleTargetKind.EMPTY_LANE,
            required=False,
        ))
    else:
        queries.append(_make_query(
            lane=VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT,
            kind=VehicleTargetKind.RESTRICTED_SNAPSHOT,
            value=frame.restricted_export.snapshot_id,
            required=True,
            snapshot=frame.restricted_export,
        ))

    ordered = tuple(sorted(
        queries,
        key=lambda row: (
            list(VehicleWatchLane).index(row.lane),
            list(VehicleTargetKind).index(row.target_kind),
            row.target_id,
        ),
    ))
    payload = _manifest_payload(
        binding=frame.binding,
        frame_revision_sha256=frame.revision_sha256,
        frame_sha256=frame.frame_sha256,
        approved_at=frame.approved_at,
        as_of=as_of,
        window_start=frame.window_start,
        queries=ordered,
    )
    return VehicleQueryManifest(
        manifest_id=_digest(payload),
        binding=frame.binding,
        frame_revision_sha256=frame.revision_sha256,
        frame_sha256=frame.frame_sha256,
        approved_at=frame.approved_at,
        as_of=as_of,
        window_start=frame.window_start,
        queries=ordered,
    )


class VehicleLead(_VehicleWatchContract):
    """Unverified record identity; never a report or candidate object."""

    lead_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_sha256: str
    query_id: str = Field(min_length=1)
    lane: VehicleWatchLane
    discovered_at: datetime
    title: str = Field(min_length=1)
    source_url: HttpUrl
    source_identity: SourceIdentity
    parent_idv_identity: Optional[SourceIdentity] = None
    order_identity: Optional[SourceIdentity] = None
    creates_candidate: Literal[False] = False

    @model_validator(mode="after")
    def _lead_preserves_exact_procurement_identites(self) -> "VehicleLead":
        _require_sha256(self.scope_sha256, "vehicle lead scope SHA-256")
        _require_aware(self.discovered_at, "vehicle lead discovered_at")
        if urlparse(str(self.source_url)).scheme.casefold() != "https":
            raise ValueError("vehicle lead source URL must use HTTPS")
        if self.lane is VehicleWatchLane.USASPENDING_ORDER_LINEAGE:
            if self.parent_idv_identity is None or self.order_identity is None:
                raise ValueError(
                    "order-lineage lead requires exact parent and order identities"
                )
        elif self.lane is VehicleWatchLane.USASPENDING_IDV:
            if self.parent_idv_identity is None:
                raise ValueError("IDV lead requires an exact parent IDV identity")
            if self.order_identity is not None:
                raise ValueError("IDV lead cannot conflate an order identity")
        elif self.order_identity is not None:
            raise ValueError("order identities belong only to the lineage lane")
        if self.parent_idv_identity is not None \
                and self.order_identity is not None \
                and self.parent_idv_identity.canonical_key \
                == self.order_identity.canonical_key:
            raise ValueError("parent IDV and order identities must remain separate")
        return self


class VehicleSearchResponse(_VehicleWatchContract):
    """Optional typed provider response for non-complete outcomes."""

    state: CoverageState = CoverageState.RETURNED
    records_returned: int = Field(default=0, ge=0)
    leads: tuple[VehicleLead, ...] = Field(default_factory=tuple)
    public_detail: str = ""

    @model_validator(mode="after")
    def _response_state_is_truthful(self) -> "VehicleSearchResponse":
        allowed = {
            CoverageState.RETURNED,
            CoverageState.PARTIAL,
            CoverageState.STALE_SNAPSHOT,
            CoverageState.FAILED,
            CoverageState.NOT_RUN,
        }
        if self.state not in allowed:
            raise ValueError("vehicle search response uses an unsupported state")
        _require_public_detail(self.public_detail)
        if self.state is CoverageState.RETURNED:
            if self.records_returned != len(self.leads):
                raise ValueError("complete response must account for every return")
        elif self.state in {CoverageState.FAILED, CoverageState.NOT_RUN}:
            if self.records_returned or self.leads:
                raise ValueError("failed or unrun response cannot carry records")
        elif len(self.leads) > self.records_returned:
            raise ValueError("usable leads exceed the provider return count")
        return self


class VehicleQueryAttempt(_VehicleWatchContract):
    query_id: str = Field(min_length=1)
    state: CoverageState
    attempted_at: Optional[datetime] = None
    records_returned: int = Field(default=0, ge=0)
    lead_ids: tuple[str, ...] = Field(default_factory=tuple)
    returned_zero: bool = False
    public_detail: str = ""

    @model_validator(mode="after")
    def _attempt_state_is_closed(self) -> "VehicleQueryAttempt":
        allowed = {
            CoverageState.RETURNED,
            CoverageState.PARTIAL,
            CoverageState.STALE_SNAPSHOT,
            CoverageState.FAILED,
            CoverageState.NOT_RUN,
            CoverageState.SCOPE_EXCLUDED,
        }
        if self.state not in allowed:
            raise ValueError("vehicle query attempt uses an unsupported state")
        _require_unique(self.lead_ids, "vehicle attempt lead IDs")
        _require_public_detail(self.public_detail)
        unattempted = {
            CoverageState.NOT_RUN,
            CoverageState.SCOPE_EXCLUDED,
        }
        if self.state in unattempted:
            if self.attempted_at is not None:
                raise ValueError("unattempted vehicle query has attempted_at")
            if self.records_returned or self.lead_ids or self.returned_zero:
                raise ValueError("unattempted vehicle query carries results")
        else:
            if self.attempted_at is None:
                raise ValueError("attempted vehicle query requires attempted_at")
            _require_aware(self.attempted_at, "vehicle attempt attempted_at")
        if self.state is CoverageState.RETURNED:
            if len(self.lead_ids) != self.records_returned:
                raise ValueError("complete query must account for every return")
            if self.returned_zero != (self.records_returned == 0):
                raise ValueError("returned-zero marker differs from result count")
        elif self.returned_zero:
            raise ValueError("returned-zero is reserved for complete queries")
        if self.state is CoverageState.FAILED \
                and (self.records_returned or self.lead_ids):
            raise ValueError("failed vehicle query cannot claim returns")
        if len(self.lead_ids) > self.records_returned:
            raise ValueError("vehicle attempt leads exceed provider returns")
        return self


class VehicleCollectionResult(_VehicleWatchContract):
    """One closed attempt per query and unverified leads only."""

    manifest: VehicleQueryManifest
    collected_at: datetime
    leads: tuple[VehicleLead, ...]
    attempts: tuple[VehicleQueryAttempt, ...]

    @model_validator(mode="after")
    def _collection_closes_every_query(self) -> "VehicleCollectionResult":
        _require_aware(self.collected_at, "vehicle collection collected_at")
        if self.collected_at > self.manifest.as_of:
            raise ValueError("vehicle collection postdates manifest as_of")
        query_ids = tuple(query.query_id for query in self.manifest.queries)
        if tuple(row.query_id for row in self.attempts) != query_ids:
            raise ValueError(
                "vehicle collection requires one ordered attempt per query"
            )
        lead_ids = tuple(lead.lead_id for lead in self.leads)
        _require_unique(lead_ids, "vehicle collection lead IDs")
        attempts = {row.query_id: row for row in self.attempts}
        queries = {row.query_id: row for row in self.manifest.queries}
        owned_ids: list[str] = []
        for lead in self.leads:
            if (lead.client_id, lead.run_id, lead.scope_sha256) != (
                self.manifest.binding.client_id,
                self.manifest.binding.run_id,
                self.manifest.binding.scope_sha256,
            ):
                raise ValueError("vehicle lead belongs to another client or run")
            if lead.discovered_at > self.manifest.as_of:
                raise ValueError("vehicle lead discovery postdates manifest as_of")
            query = queries.get(lead.query_id)
            if query is None:
                raise ValueError("vehicle lead references an unknown query")
            if lead.lane is not query.lane:
                raise ValueError("vehicle lead lane differs from its query")
            if lead.lead_id not in attempts[lead.query_id].lead_ids:
                raise ValueError("vehicle lead is absent from its query attempt")
            if query.target_kind is VehicleTargetKind.CANDIDATE_SOURCE \
                    and lead.source_identity.canonical_key \
                    != query.target_source_identity.canonical_key:
                raise ValueError("candidate-source result changed exact identity")
            if query.target_kind is VehicleTargetKind.KNOWN_PARENT_IDV:
                if lead.parent_idv_identity is None \
                        or lead.parent_idv_identity.canonical_key \
                        != query.target_source_identity.canonical_key:
                    raise ValueError("vehicle result changed exact parent identity")
            owned_ids.append(lead.lead_id)
        _require_unique(owned_ids, "vehicle attempt lead ownership")
        attempted_ids = tuple(
            lead_id for attempt in self.attempts for lead_id in attempt.lead_ids
        )
        _require_unique(attempted_ids, "vehicle attempt lead ownership")
        if set(attempted_ids) != set(lead_ids):
            raise ValueError("vehicle attempt has an unresolved lead reference")
        for query, attempt in zip(self.manifest.queries, self.attempts):
            if not query.required \
                    and attempt.state is not CoverageState.SCOPE_EXCLUDED:
                raise ValueError("scope-excluded vehicle query was attempted")
            if query.required \
                    and attempt.state is CoverageState.SCOPE_EXCLUDED:
                raise ValueError("required vehicle query was scope excluded")
            if attempt.attempted_at is not None \
                    and attempt.attempted_at > self.manifest.as_of:
                raise ValueError("vehicle attempt postdates manifest as_of")
        return self


class VehicleWatchSearcher(Protocol):
    """Injected provider; implementations may perform authorized live work."""

    def __call__(
        self,
        query: VehicleQuerySpec,
    ) -> Union[
        VehicleSearchResponse,
        Iterable[Union[VehicleLead, Mapping[str, object]]],
    ]: ...


def _validated_leads(
    query: VehicleQuerySpec,
    values: Iterable[Union[VehicleLead, Mapping[str, object]]],
    attempted_at: datetime,
) -> tuple[VehicleLead, ...]:
    if isinstance(values, (str, bytes, Mapping)):
        raise TypeError("vehicle searcher must return an iterable of lead rows")
    by_id: dict[str, VehicleLead] = {}
    for value in values:
        lead = value if isinstance(value, VehicleLead) \
            else VehicleLead.model_validate(value)
        if lead.query_id != query.query_id:
            raise ValueError("vehicle searcher returned a lead for another query")
        if lead.lane is not query.lane:
            raise ValueError("vehicle searcher returned a lead for another lane")
        if lead.discovered_at > attempted_at:
            raise ValueError("vehicle lead discovery postdates its attempt")
        if lead.lead_id in by_id:
            raise ValueError("vehicle searcher returned duplicate lead IDs")
        by_id[lead.lead_id] = lead
    return tuple(sorted(
        by_id.values(),
        key=lambda row: (
            row.source_identity.canonical_key,
            row.parent_idv_identity.canonical_key
            if row.parent_idv_identity else "",
            row.order_identity.canonical_key if row.order_identity else "",
            row.lead_id,
        ),
    ))


def run_vehicle_watch_collection(
    manifest: VehicleQueryManifest,
    searcher: Union[
        VehicleWatchSearcher,
        Callable[[VehicleQuerySpec], Union[
            VehicleSearchResponse,
            Iterable[Union[VehicleLead, Mapping[str, object]]],
        ]],
    ],
    attempted_at: datetime,
) -> VehicleCollectionResult:
    """Execute an injected provider and preserve every coverage outcome."""

    _require_aware(attempted_at, "vehicle collection attempted_at")
    if attempted_at > manifest.as_of:
        raise ValueError("vehicle collection attempted_at postdates as_of")
    all_leads: list[VehicleLead] = []
    attempts: list[VehicleQueryAttempt] = []
    for query in manifest.queries:
        if not query.required:
            detail = (
                "No operator-supplied restricted export was present."
                if query.lane
                is VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT
                else "No approved target was present for this lane."
            )
            attempts.append(VehicleQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.SCOPE_EXCLUDED,
                public_detail=detail,
            ))
            continue
        try:
            raw = searcher(query)
        except Exception:  # noqa: BLE001 - provider detail never crosses boundary
            attempts.append(VehicleQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.FAILED,
                attempted_at=attempted_at,
                public_detail=(
                    "The collection provider did not return a usable response."
                ),
            ))
            continue

        if isinstance(raw, VehicleSearchResponse):
            leads = _validated_leads(query, raw.leads, attempted_at)
            state = raw.state
            records_returned = raw.records_returned
            detail = raw.public_detail
        else:
            if raw is None:
                raise TypeError("vehicle searcher returned no iterable")
            leads = _validated_leads(query, raw, attempted_at)
            state = CoverageState.RETURNED
            records_returned = len(leads)
            detail = (
                "Collection completed with no leads."
                if not leads
                else f"Collection returned {len(leads)} unverified lead(s)."
            )

        restricted_is_stale = (
            query.lane is VehicleWatchLane.AUTHORIZED_RESTRICTED_EXPORT
            and query.restricted_fresh_through is not None
            and manifest.as_of.date() > query.restricted_fresh_through
        )
        if restricted_is_stale and state in {
            CoverageState.RETURNED,
            CoverageState.PARTIAL,
        }:
            state = CoverageState.STALE_SNAPSHOT
            detail = "The supplied restricted snapshot is outside freshness."
        attempt_time = None if state is CoverageState.NOT_RUN else attempted_at
        all_leads.extend(leads)
        attempts.append(VehicleQueryAttempt(
            query_id=query.query_id,
            state=state,
            attempted_at=attempt_time,
            records_returned=records_returned,
            lead_ids=tuple(lead.lead_id for lead in leads),
            returned_zero=(
                state is CoverageState.RETURNED and records_returned == 0
            ),
            public_detail=detail,
        ))
    return VehicleCollectionResult(
        manifest=manifest,
        collected_at=attempted_at,
        leads=tuple(all_leads),
        attempts=tuple(attempts),
    )


def project_vehicle_watch_coverage(
    result: VehicleCollectionResult,
    accepted_evidence_by_query: Optional[
        Mapping[str, Iterable[EvidenceRecord]]
    ] = None,
) -> tuple[CoverageRecord, ...]:
    """Project exact query attempts into the shared coverage receipt shape.

    Accepted evidence must resolve to an exact source, parent, or order
    identity observed by that query.  A discovery lead alone cannot be counted
    as accepted evidence and cannot become a ``VehicleSignal`` here.
    """

    supplied = accepted_evidence_by_query or {}
    queries = {row.query_id: row for row in result.manifest.queries}
    unknown = set(supplied) - set(queries)
    if unknown:
        raise ValueError("vehicle coverage references an unknown query")
    leads_by_query: dict[str, tuple[VehicleLead, ...]] = {
        query_id: tuple(
            lead for lead in result.leads if lead.query_id == query_id
        )
        for query_id in queries
    }
    attempts = {row.query_id: row for row in result.attempts}
    rows: list[CoverageRecord] = []
    for query in result.manifest.queries:
        attempt = attempts[query.query_id]
        evidence_rows = tuple(supplied.get(query.query_id, ()))
        evidence_ids = tuple(row.evidence_id for row in evidence_rows)
        _require_unique(evidence_ids, "vehicle coverage evidence IDs")
        allowed_identities = {
            identity.canonical_key
            for lead in leads_by_query[query.query_id]
            for identity in (
                lead.source_identity,
                lead.parent_idv_identity,
                lead.order_identity,
            )
            if identity is not None
        }
        for evidence in evidence_rows:
            if (evidence.client_id, evidence.run_id, evidence.scope_sha256) != (
                result.manifest.binding.client_id,
                result.manifest.binding.run_id,
                result.manifest.binding.scope_sha256,
            ):
                raise ValueError("accepted evidence belongs to another client")
            if evidence.retrieved_at > result.manifest.as_of:
                raise ValueError("accepted vehicle evidence postdates as_of")
            if not evidence.primary_source or evidence.source_kind not in {
                EvidenceKind.VEHICLE,
                EvidenceKind.NOTICE,
                EvidenceKind.AWARD,
                EvidenceKind.AGENCY_FORECAST,
                EvidenceKind.AGENCY_ANNOUNCEMENT,
            }:
                raise ValueError(
                    "accepted vehicle evidence must be primary procurement "
                    "evidence"
                )
            if query.lane in PUBLIC_VEHICLE_WATCH_LANES \
                    and not evidence.official_source:
                raise ValueError(
                    "public vehicle lanes require official evidence"
                )
            if evidence.source_identity.canonical_key not in allowed_identities:
                raise ValueError(
                    "accepted evidence does not resolve an observed identity"
                )
        if evidence_rows and attempt.state in {
            CoverageState.FAILED,
            CoverageState.NOT_RUN,
            CoverageState.SCOPE_EXCLUDED,
        }:
            raise ValueError("incomplete vehicle query cannot accept evidence")
        if len(evidence_rows) > attempt.records_returned:
            raise ValueError("accepted evidence exceeds returned vehicle records")
        rows.append(CoverageRecord(
            client_id=result.manifest.binding.client_id,
            run_id=result.manifest.binding.run_id,
            scope_sha256=result.manifest.binding.scope_sha256,
            source=query.lane.value,
            query_family=f"vehicle_watch:{query.target_kind.value}",
            query_required=query.required,
            state=attempt.state,
            window_start=result.manifest.window_start,
            window_end=result.manifest.as_of.date(),
            attempted_at=attempt.attempted_at,
            records_returned=attempt.records_returned,
            records_accepted=len(evidence_rows),
            accepted_evidence_ids=evidence_ids,
            query_manifest_id=result.manifest.manifest_id,
            query_id=query.query_id,
            watch_target_id=query.target_id,
            public_detail=attempt.public_detail,
        ))
    return tuple(rows)


__all__ = (
    "PUBLIC_VEHICLE_WATCH_LANES",
    "VEHICLE_WATCH_SCHEMA_VERSION",
    "RestrictedVehicleExport",
    "VehicleCollectionResult",
    "VehicleLead",
    "VehicleQueryAttempt",
    "VehicleQueryManifest",
    "VehicleQuerySpec",
    "VehicleSearchResponse",
    "VehicleTargetKind",
    "VehicleWatchFrame",
    "VehicleWatchLane",
    "VehicleWatchSearcher",
    "build_vehicle_query_manifest",
    "project_vehicle_watch_coverage",
    "run_vehicle_watch_collection",
)
