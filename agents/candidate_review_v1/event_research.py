"""Deterministic event-discovery contracts for Candidate Review v1.

This module owns only the *discovery* side of the federal-event lane.  It
builds a complete, client-bound query census and records what an injected
search provider returned.  It deliberately cannot construct ``EvidenceRecord``
or ``EventRecord``: a later verification lane must fetch an authoritative
agency or organizer page, prove the stated facts, and bind exact assertion
spans before an event can enter a report.

There are no network, filesystem, clock, or model calls here.  Callers supply
both the search function and the attempt timestamp, which keeps replays and
tests byte-for-byte deterministic.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from enum import Enum
import hashlib
from ipaddress import ip_address
import json
import re
from typing import Callable, Iterable, Literal, Mapping, Optional, Protocol, Union
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CoverageState,
)


MANIFEST_VERSION = "candidate_review_v1.event_queries.v2"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_DETAIL_RE = re.compile(
    r"(?:traceback|api[_ -]?key|authorization:|bearer\s+[a-z0-9._-]+)",
    re.IGNORECASE,
)
# Sanity rail against malformed frames, never a relevance filter: an
# all-federal sweep legitimately carries 100s of office-level accounts
# (operator rule 2026-08-04). Matches the watch builder's identity bound.
_MAX_FRAME_VALUES = 1024
_SHARED_HOSTING_SUFFIXES = frozenset({
    "amazonaws.com",
    "appspot.com",
    "azurewebsites.net",
    "blogspot.com",
    "cloudfront.net",
    "firebaseapp.com",
    "github.io",
    "herokuapp.com",
    "netlify.app",
    "notion.site",
    "pages.dev",
    "readthedocs.io",
    "surge.sh",
    "vercel.app",
    "web.app",
    "wixsite.com",
})


class _ResearchContract(BaseModel):
    """Strict immutable boundary shared by event-discovery models."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _require_sha256(value: str, label: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_unique(values: Iterable[str], label: str) -> None:
    rows = tuple(values)
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} must be unique")


def _require_public_detail(value: str) -> None:
    if _SAFE_DETAIL_RE.search(value):
        raise ValueError("public detail contains sensitive provider internals")


def _normalized_values(values: tuple[str, ...]) -> tuple[str, ...]:
    """Stable query values independent of analyst/editor input ordering."""

    return tuple(sorted(
        (" ".join(value.split()) for value in values),
        key=lambda value: (value.casefold(), value),
    ))


def _validate_frame_values(values: tuple[str, ...], label: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{label} cannot contain blank values")
    if len(values) > _MAX_FRAME_VALUES:
        raise ValueError(
            f"{label} cannot contain more than {_MAX_FRAME_VALUES} values"
        )
    _require_unique(
        (" ".join(value.split()).casefold() for value in values),
        label,
    )


def normalize_trusted_organizer_domain(value: str) -> str:
    """Return one safe registrable or full organizer hostname.

    Public suffixes and shared-hosting tenant suffixes are deliberately not
    trustable as a whole.  A caller may trust a specific tenant host such as
    ``organizer.github.io``, but never every tenant under ``github.io``.
    """

    raw = value.strip().casefold()
    parsed = urlparse(f"//{raw}")
    host = (parsed.hostname or "").casefold()
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or raw.startswith(("http://", "https://"))
    ):
        raise ValueError(
            "trusted organizer domains must be bare DNS hostnames"
        )
    if host == "localhost" or "." not in host:
        raise ValueError(
            "trusted organizer domains must be public DNS hostnames"
        )
    labels = host.split(".")
    if any(
        re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
            label,
        ) is None
        for label in labels
    ):
        raise ValueError(
            "trusted organizer domains must be valid DNS hostnames"
        )
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError(
            "trusted organizer domains cannot be IP addresses"
        )
    country_second_levels = {
        "ac", "co", "com", "edu", "gov", "net", "org",
    }
    if len(labels) == 2 and len(labels[-1]) == 2 \
            and labels[-2] in country_second_levels:
        raise ValueError(
            "trusted organizer domains must be registrable or full hostnames"
        )
    if host in _SHARED_HOSTING_SUFFIXES:
        raise ValueError(
            "trusted organizer domains cannot authorize a shared-hosting suffix"
        )
    return host


def _add_months(day: date, months: int) -> date:
    """Exact calendar-month arithmetic with end-of-month clamping."""

    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(
        year,
        month,
        min(day.day, calendar.monthrange(year, month)[1]),
    )


def _digest(payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class EventQueryFamily(str, Enum):
    CAPABILITIES_MISSIONS = "capabilities_missions"
    PRIORITY_AGENCIES_COMPONENTS = "priority_agencies_components"
    NAICS_PSC = "naics_psc"
    CANDIDATE_ACCOUNTS = "candidate_accounts"
    VEHICLE_PARTNER_COMPETITOR_ECOSYSTEM = \
        "vehicle_partner_competitor_ecosystem"
    BUYER_COMMUNITIES = "buyer_communities"
    POLICY_BUDGET_THEMES = "policy_budget_themes"
    STANDING_ORGANIZER = "standing_organizer"
    STANDING_EVENT_SERIES = "standing_event_series"


class EventSourceClass(str, Enum):
    OFFICIAL_AGENCY = "official_agency"
    OFFICIAL_ORGANIZER = "official_organizer"
    OFFICIAL_AGENDA_ATTENDANCE = "official_agenda_attendance"
    OFFICIAL_REGISTRATION_VENUE = "official_registration_venue"
    DISCOVERY_INDEX = "discovery_index"


CLIENT_EVENT_QUERY_FAMILIES = (
    EventQueryFamily.CAPABILITIES_MISSIONS,
    EventQueryFamily.PRIORITY_AGENCIES_COMPONENTS,
    EventQueryFamily.NAICS_PSC,
    EventQueryFamily.CANDIDATE_ACCOUNTS,
    EventQueryFamily.VEHICLE_PARTNER_COMPETITOR_ECOSYSTEM,
    EventQueryFamily.BUYER_COMMUNITIES,
    EventQueryFamily.POLICY_BUDGET_THEMES,
)

CLIENT_EVENT_SOURCE_CLASSES = (
    EventSourceClass.OFFICIAL_AGENCY,
    EventSourceClass.OFFICIAL_ORGANIZER,
    EventSourceClass.OFFICIAL_AGENDA_ATTENDANCE,
    EventSourceClass.OFFICIAL_REGISTRATION_VENUE,
)


class EventResearchFrame(_ResearchContract):
    """The analyst-approved frame from which the query census is derived."""

    binding: ArtifactBinding
    revision_sha256: str
    capabilities_missions: tuple[str, ...] = Field(default_factory=tuple)
    priority_agencies_components: tuple[str, ...] = Field(
        default_factory=tuple)
    naics_psc: tuple[str, ...] = Field(default_factory=tuple)
    candidate_accounts: tuple[str, ...] = Field(default_factory=tuple)
    vehicle_partner_competitor_ecosystem: tuple[str, ...] = Field(
        default_factory=tuple)
    buyer_communities: tuple[str, ...] = Field(default_factory=tuple)
    policy_budget_themes: tuple[str, ...] = Field(default_factory=tuple)
    trusted_organizer_domains: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _frame_is_canonical_and_bounded(self) -> "EventResearchFrame":
        _require_sha256(self.revision_sha256, "event research revision_sha256")
        for field_name in (
            "capabilities_missions",
            "priority_agencies_components",
            "naics_psc",
            "candidate_accounts",
            "vehicle_partner_competitor_ecosystem",
            "buyer_communities",
            "policy_budget_themes",
        ):
            _validate_frame_values(getattr(self, field_name), field_name)
        _validate_frame_values(
            self.trusted_organizer_domains,
            "trusted_organizer_domains",
        )
        for domain in self.trusted_organizer_domains:
            normalize_trusted_organizer_domain(domain)
        _require_unique(
            (domain.casefold() for domain in self.trusted_organizer_domains),
            "trusted organizer domains",
        )
        return self

    def values_for(self, family: EventQueryFamily) -> tuple[str, ...]:
        """Return the approved terms for one fixed query family."""

        return getattr(self, family.value)


class EventQuerySpec(_ResearchContract):
    query_id: str = Field(min_length=1)
    query_family: EventQueryFamily
    source_class: EventSourceClass
    query_text: str = Field(min_length=1)
    required: bool
    watch_target_id: Optional[str] = None
    watch_target_kind: Optional[Literal["organizer", "event_series"]] = None
    watch_priority: Optional[Literal["tier_1", "watch"]] = None
    source_role: Optional[
        Literal["organizer", "operator", "discovery_only"]
    ] = None
    watch_identity_status: Optional[Literal["verified", "pending"]] = None
    official_hosts: tuple[str, ...] = Field(default_factory=tuple)
    nonrequired_reason: Optional[
        Literal["empty_client_frame", "scope_excluded"]
    ] = None

    @model_validator(mode="after")
    def _watch_metadata_matches_query_family(self) -> "EventQuerySpec":
        standing = self.query_family in {
            EventQueryFamily.STANDING_ORGANIZER,
            EventQueryFamily.STANDING_EVENT_SERIES,
        }
        metadata = (
            self.watch_target_id,
            self.watch_target_kind,
            self.watch_priority,
            self.source_role,
            self.watch_identity_status,
        )
        if standing and any(value is None for value in metadata):
            raise ValueError("standing watch query requires complete target metadata")
        if not standing and (
            any(value is not None for value in metadata)
            or self.official_hosts
        ):
            raise ValueError("client-frame query cannot carry watch metadata")
        if not standing:
            if self.source_class not in CLIENT_EVENT_SOURCE_CLASSES:
                raise ValueError("client-frame query uses a non-client source class")
            if self.required != (self.nonrequired_reason is None):
                raise ValueError(
                    "client-frame query required state needs its exact reason"
                )
            if self.nonrequired_reason not in {None, "empty_client_frame"}:
                raise ValueError("client-frame query has an invalid skip reason")
            return self
        expected_kind = (
            "organizer"
            if self.query_family == EventQueryFamily.STANDING_ORGANIZER
            else "event_series"
        )
        if self.watch_target_kind != expected_kind:
            raise ValueError("watch target kind differs from its query family")
        normalized_hosts = tuple(sorted(
            (normalize_trusted_organizer_domain(value)
             for value in self.official_hosts),
            key=lambda value: (value.casefold(), value),
        ))
        if self.official_hosts != normalized_hosts:
            raise ValueError("watch query official hosts are not canonical")
        if self.required != (self.nonrequired_reason is None):
            raise ValueError(
                "standing watch query required state needs its exact reason"
            )
        if self.nonrequired_reason not in {None, "scope_excluded"}:
            raise ValueError("standing watch query has an invalid skip reason")
        discovery_only = (
            self.source_role == "discovery_only"
            or self.watch_identity_status == "pending"
        )
        if self.watch_identity_status == "pending" and self.official_hosts:
            raise ValueError("pending watch identity cannot authorize hosts")
        if discovery_only:
            if self.source_class != EventSourceClass.DISCOVERY_INDEX:
                raise ValueError(
                    "pending or discovery-only target must use discovery-index "
                    "queries"
                )
        elif self.source_class == EventSourceClass.DISCOVERY_INDEX:
            raise ValueError(
                "official organizer/operator target cannot use discovery index"
            )
        return self


def _query_id(
    family: EventQueryFamily,
    source_class: EventSourceClass,
    query_text: str,
    required: bool,
    *,
    watch_target_id: Optional[str] = None,
    watch_target_kind: Optional[str] = None,
    watch_priority: Optional[str] = None,
    source_role: Optional[str] = None,
    watch_identity_status: Optional[str] = None,
    official_hosts: tuple[str, ...] = (),
    nonrequired_reason: Optional[str] = None,
) -> str:
    digest = _digest({
        "version": MANIFEST_VERSION,
        "query_family": family.value,
        "source_class": source_class.value,
        "query_text": query_text,
        "required": required,
        "watch_target_id": watch_target_id,
        "watch_target_kind": watch_target_kind,
        "watch_priority": watch_priority,
        "source_role": source_role,
        "watch_identity_status": watch_identity_status,
        "official_hosts": official_hosts,
        "nonrequired_reason": nonrequired_reason,
    })[:16]
    return f"event-query:{family.value}:{source_class.value}:{digest}"


def _query_payload(query: EventQuerySpec) -> dict[str, object]:
    return {
        "query_id": query.query_id,
        "query_family": query.query_family.value,
        "source_class": query.source_class.value,
        "query_text": query.query_text,
        "required": query.required,
        "watch_target_id": query.watch_target_id,
        "watch_target_kind": query.watch_target_kind,
        "watch_priority": query.watch_priority,
        "source_role": query.source_role,
        "watch_identity_status": query.watch_identity_status,
        "official_hosts": query.official_hosts,
        "nonrequired_reason": query.nonrequired_reason,
    }


def _manifest_basis(
    *,
    binding: ArtifactBinding,
    frame_revision_sha256: str,
    window_start: date,
    confirmed_event_through: date,
    flagship_event_through: date,
    trusted_organizer_domains: tuple[str, ...],
    watch_universe_sha256: Optional[str],
    watch_target_ids: tuple[str, ...],
    applicable_watch_target_ids: tuple[str, ...],
    search_due_watch_target_ids: tuple[str, ...],
    cadence_deferred_watch_target_ids: tuple[str, ...],
    scope_excluded_watch_target_ids: tuple[str, ...],
    queries: tuple[EventQuerySpec, ...],
    purpose: str,
) -> dict[str, object]:
    return {
        "purpose": purpose,
        "version": MANIFEST_VERSION,
        "binding": binding.model_dump(mode="json"),
        "frame_revision_sha256": frame_revision_sha256,
        "window_start": window_start.isoformat(),
        "confirmed_event_through": confirmed_event_through.isoformat(),
        "flagship_event_through": flagship_event_through.isoformat(),
        "trusted_organizer_domains": trusted_organizer_domains,
        "watch_universe_sha256": watch_universe_sha256,
        "watch_target_ids": watch_target_ids,
        "applicable_watch_target_ids": applicable_watch_target_ids,
        "search_due_watch_target_ids": search_due_watch_target_ids,
        "cadence_deferred_watch_target_ids": (
            cadence_deferred_watch_target_ids
        ),
        "scope_excluded_watch_target_ids": scope_excluded_watch_target_ids,
        "queries": [_query_payload(item) for item in queries],
    }


class EventQueryManifest(_ResearchContract):
    """Base 7-by-4 census plus target-level standing-watch queries."""

    manifest_id: str
    cache_key: str
    version: str = MANIFEST_VERSION
    binding: ArtifactBinding
    frame_revision_sha256: str
    window_start: date
    confirmed_event_through: date
    flagship_event_through: date
    trusted_organizer_domains: tuple[str, ...]
    watch_universe_sha256: Optional[str] = None
    watch_target_ids: tuple[str, ...] = Field(default_factory=tuple)
    applicable_watch_target_ids: tuple[str, ...] = Field(default_factory=tuple)
    search_due_watch_target_ids: tuple[str, ...] = Field(default_factory=tuple)
    cadence_deferred_watch_target_ids: tuple[str, ...] = Field(
        default_factory=tuple)
    scope_excluded_watch_target_ids: tuple[str, ...] = Field(
        default_factory=tuple)
    queries: tuple[EventQuerySpec, ...]

    @model_validator(mode="after")
    def _manifest_is_complete_and_recomputable(self) -> "EventQueryManifest":
        if self.version != MANIFEST_VERSION:
            raise ValueError("event query manifest version is unsupported")
        _require_sha256(self.manifest_id, "event query manifest_id")
        _require_sha256(self.cache_key, "event query cache_key")
        _require_sha256(
            self.frame_revision_sha256,
            "event query frame_revision_sha256",
        )
        if self.confirmed_event_through != _add_months(self.window_start, 12):
            raise ValueError(
                "confirmed event horizon must be exactly 12 calendar months"
            )
        if self.flagship_event_through != _add_months(self.window_start, 18):
            raise ValueError(
                "flagship event horizon must be exactly 18 calendar months"
            )
        normalized_domains = tuple(sorted(
            (
                normalize_trusted_organizer_domain(domain)
                for domain in self.trusted_organizer_domains
            ),
            key=lambda value: (value.casefold(), value),
        ))
        if self.trusted_organizer_domains != normalized_domains:
            raise ValueError(
                "event query manifest organizer domains are not canonical"
            )
        base_count = len(CLIENT_EVENT_QUERY_FAMILIES) * len(
            CLIENT_EVENT_SOURCE_CLASSES)
        expected_pairs = tuple(
            (family, source_class)
            for family in CLIENT_EVENT_QUERY_FAMILIES
            for source_class in CLIENT_EVENT_SOURCE_CLASSES
        )
        actual_pairs = tuple(
            (query.query_family, query.source_class)
            for query in self.queries[:base_count]
        )
        if actual_pairs != expected_pairs:
            raise ValueError(
                "event query manifest requires the exact ordered 7x4 census"
            )
        watch_queries = self.queries[base_count:]
        watch_fields = (
            self.watch_target_ids,
            self.applicable_watch_target_ids,
            self.search_due_watch_target_ids,
            self.cadence_deferred_watch_target_ids,
            self.scope_excluded_watch_target_ids,
        )
        if self.watch_universe_sha256 is None:
            if any(watch_fields) or watch_queries:
                raise ValueError(
                    "standing watch queries require a bound universe digest"
                )
        else:
            _require_sha256(
                self.watch_universe_sha256,
                "event watch universe SHA-256",
            )
            _require_unique(self.watch_target_ids, "watch target IDs")
            _require_unique(
                self.applicable_watch_target_ids,
                "applicable watch target IDs",
            )
            _require_unique(
                self.search_due_watch_target_ids,
                "search-due watch target IDs",
            )
            _require_unique(
                self.cadence_deferred_watch_target_ids,
                "cadence-deferred watch target IDs",
            )
            _require_unique(
                self.scope_excluded_watch_target_ids,
                "scope-excluded watch target IDs",
            )
            applicable = set(self.applicable_watch_target_ids)
            search_due = set(self.search_due_watch_target_ids)
            cadence_deferred = set(self.cadence_deferred_watch_target_ids)
            excluded = set(self.scope_excluded_watch_target_ids)
            if applicable & excluded or applicable | excluded != set(
                self.watch_target_ids
            ):
                raise ValueError(
                    "watch applicability must partition every target exactly"
                )
            if cadence_deferred:
                raise ValueError(
                    "applicable standing-watch targets cannot be cadence deferred"
                )
            if search_due != applicable:
                raise ValueError(
                    "every applicable standing-watch target must be search due"
                )
            observed_target_ids = tuple(dict.fromkeys(
                query.watch_target_id for query in watch_queries
            ))
            if observed_target_ids != self.watch_target_ids:
                raise ValueError(
                    "watch queries do not preserve exact target ordering"
                )
            for query in watch_queries:
                if query.watch_target_id not in applicable | excluded:
                    raise ValueError("watch query references an unknown target")
                target_id = query.watch_target_id
                if query.required != (target_id in search_due):
                    raise ValueError(
                        "watch query required state differs from search cadence"
                    )
                expected_reason = (
                    None if target_id in search_due else "scope_excluded"
                )
                if query.nonrequired_reason != expected_reason:
                    raise ValueError(
                        "watch query skip reason differs from its partition"
                    )
            query_pairs = tuple(
                (query.watch_target_id, query.source_class)
                for query in watch_queries
            )
            _require_unique(query_pairs, "watch target/source query pairs")
        _require_unique(
            (query.query_id for query in self.queries),
            "event query IDs",
        )
        for query in self.queries:
            expected_query_id = _query_id(
                query.query_family,
                query.source_class,
                query.query_text,
                query.required,
                watch_target_id=query.watch_target_id,
                watch_target_kind=query.watch_target_kind,
                watch_priority=query.watch_priority,
                source_role=query.source_role,
                watch_identity_status=query.watch_identity_status,
                official_hosts=query.official_hosts,
                nonrequired_reason=query.nonrequired_reason,
            )
            if query.query_id != expected_query_id:
                raise ValueError("event query identity does not match its content")
        common = dict(
            binding=self.binding,
            frame_revision_sha256=self.frame_revision_sha256,
            window_start=self.window_start,
            confirmed_event_through=self.confirmed_event_through,
            flagship_event_through=self.flagship_event_through,
            trusted_organizer_domains=self.trusted_organizer_domains,
            watch_universe_sha256=self.watch_universe_sha256,
            watch_target_ids=self.watch_target_ids,
            applicable_watch_target_ids=self.applicable_watch_target_ids,
            search_due_watch_target_ids=self.search_due_watch_target_ids,
            cadence_deferred_watch_target_ids=(
                self.cadence_deferred_watch_target_ids
            ),
            scope_excluded_watch_target_ids=(
                self.scope_excluded_watch_target_ids
            ),
            queries=self.queries,
        )
        if self.manifest_id != _digest(_manifest_basis(
            **common,
            purpose="event-query-manifest",
        )):
            raise ValueError("event query manifest_id does not match its content")
        if self.cache_key != _digest(_manifest_basis(
            **common,
            purpose="event-query-cache",
        )):
            raise ValueError("event query cache_key does not match its content")
        return self


class EventLead(_ResearchContract):
    """Unverified discovery lead; never report-publishable on its own."""

    lead_id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    source_class: EventSourceClass
    discovered_at: datetime
    title: str = Field(min_length=1)
    url: HttpUrl
    claimed_date: Optional[str] = None
    claimed_location: Optional[str] = None
    claimed_organizer: Optional[str] = None

    @model_validator(mode="after")
    def _lead_is_traceable_but_unverified(self) -> "EventLead":
        _require_aware(self.discovered_at, "event lead discovered_at")
        if urlparse(str(self.url)).scheme.casefold() != "https":
            raise ValueError("event discovery leads must use HTTPS URLs")
        return self


class EventSearchResponse(_ResearchContract):
    """Typed provider response that preserves incomplete coverage states."""

    state: CoverageState = CoverageState.RETURNED
    records_returned: int = Field(default=0, ge=0)
    leads: tuple[EventLead, ...] = Field(default_factory=tuple)
    public_detail: str = ""

    @model_validator(mode="after")
    def _response_state_is_truthful(self) -> "EventSearchResponse":
        allowed = {
            CoverageState.RETURNED,
            CoverageState.PARTIAL,
            CoverageState.STALE_SNAPSHOT,
            CoverageState.FAILED,
            CoverageState.NOT_RUN,
        }
        if self.state not in allowed:
            raise ValueError("event search response uses an unsupported state")
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


class EventQueryAttempt(_ResearchContract):
    query_id: str = Field(min_length=1)
    state: CoverageState
    attempted_at: Optional[datetime] = None
    records_returned: int = Field(default=0, ge=0)
    lead_ids: tuple[str, ...] = Field(default_factory=tuple)
    public_detail: str = ""

    @model_validator(mode="after")
    def _attempt_state_is_truthful(self) -> "EventQueryAttempt":
        _require_unique(self.lead_ids, "event attempt lead IDs")
        _require_public_detail(self.public_detail)
        unattempted = {
            CoverageState.NOT_RUN,
            CoverageState.SCOPE_EXCLUDED,
            CoverageState.NOT_USED,
        }
        if self.state in unattempted:
            if self.attempted_at is not None:
                raise ValueError("unattempted event query cannot carry attempted_at")
            if self.records_returned or self.lead_ids:
                raise ValueError("unattempted event query cannot carry results")
        else:
            if self.attempted_at is None:
                raise ValueError("attempted event query requires attempted_at")
            _require_aware(self.attempted_at, "event query attempted_at")
        if len(self.lead_ids) > self.records_returned:
            raise ValueError("event attempt lead count exceeds returned records")
        if self.state == CoverageState.RETURNED \
                and len(self.lead_ids) != self.records_returned:
            raise ValueError("complete event query must account for every return")
        if self.state == CoverageState.FAILED \
                and (self.records_returned or self.lead_ids):
            raise ValueError("failed event query cannot claim returned leads")
        return self


class EventDiscoveryResult(_ResearchContract):
    """Complete discovery output and one attempt row per manifest query."""

    manifest: EventQueryManifest
    attempted_at: datetime
    leads: tuple[EventLead, ...]
    attempts: tuple[EventQueryAttempt, ...]

    @model_validator(mode="after")
    def _result_closes_every_query_and_lead(self) -> "EventDiscoveryResult":
        _require_aware(self.attempted_at, "event discovery attempted_at")
        query_ids = tuple(query.query_id for query in self.manifest.queries)
        attempt_ids = tuple(attempt.query_id for attempt in self.attempts)
        if attempt_ids != query_ids:
            raise ValueError(
                "event discovery requires one ordered attempt per manifest query"
            )
        lead_ids = tuple(lead.lead_id for lead in self.leads)
        _require_unique(lead_ids, "event discovery lead IDs")
        specs = {query.query_id: query for query in self.manifest.queries}
        attempts = {attempt.query_id: attempt for attempt in self.attempts}
        for lead in self.leads:
            spec = specs.get(lead.query_id)
            if spec is None:
                raise ValueError("event lead references an unknown query")
            if lead.source_class != spec.source_class:
                raise ValueError("event lead source class differs from its query")
            if lead.lead_id not in attempts[lead.query_id].lead_ids:
                raise ValueError("event lead is absent from its query attempt")
        attempted_lead_ids = tuple(
            lead_id
            for attempt in self.attempts
            for lead_id in attempt.lead_ids
        )
        _require_unique(attempted_lead_ids, "event attempt lead ownership")
        if set(attempted_lead_ids) != set(lead_ids):
            raise ValueError("event attempt contains an unresolved lead reference")
        return self


class EventDiscoverySearcher(Protocol):
    """Injected discovery provider; implementations may perform live work."""

    def __call__(
        self,
        query: EventQuerySpec,
    ) -> Union[
        EventSearchResponse,
        Iterable[Union[EventLead, Mapping[str, object]]],
    ]: ...


_SOURCE_INSTRUCTIONS = {
    EventSourceClass.OFFICIAL_AGENCY: (
        "official federal agency calendar announcement industry day vendor "
        "engagement"
    ),
    EventSourceClass.OFFICIAL_ORGANIZER: (
        "official organizer conference summit event"
    ),
    EventSourceClass.OFFICIAL_AGENDA_ATTENDANCE: (
        "official agenda speakers exhibitors federal agency participation"
    ),
    EventSourceClass.OFFICIAL_REGISTRATION_VENUE: (
        "official registration venue event dates"
    ),
    EventSourceClass.DISCOVERY_INDEX: (
        "event discovery index lead requiring separate official verification"
    ),
}


def _query_text(
    *,
    family: EventQueryFamily,
    source_class: EventSourceClass,
    values: tuple[str, ...],
    trusted_domains: tuple[str, ...],
    window_start: date,
    flagship_through: date,
) -> str:
    if not values:
        return (
            f"No approved {family.value.replace('_', ' ')} inputs; "
            f"{source_class.value.replace('_', ' ')} query is scope excluded."
        )
    terms = " OR ".join(f'"{value}"' for value in values)
    domain_hint = ""
    if source_class != EventSourceClass.OFFICIAL_AGENCY and trusted_domains:
        domain_hint = " trusted organizer domains: " + ", ".join(
            trusted_domains)
    return (
        f"{_SOURCE_INSTRUCTIONS[source_class]} for {terms}; "
        f"event window {window_start.isoformat()} through "
        f"{flagship_through.isoformat()}.{domain_hint}"
    )


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _watch_target_values(target: object) -> dict[str, object]:
    try:
        target_id = str(getattr(target, "id"))
        kind = str(_enum_value(getattr(target, "kind")))
        canonical_name = str(getattr(target, "canonical_name"))
        priority = str(_enum_value(getattr(target, "priority")))
        role = str(_enum_value(getattr(target, "role")))
        identity_status = str(
            _enum_value(getattr(target, "identity_status"))
        )
        official_hosts = tuple(getattr(target, "official_hosts"))
        query_aliases = tuple(getattr(target, "query_aliases"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("standing watch target has invalid integration fields") \
            from exc
    if not target_id or not canonical_name or kind not in {
        "organizer", "event_series",
    } or role not in {"organizer", "operator", "discovery_only"}:
        raise ValueError("standing watch target has invalid identity or role")
    if priority not in {"tier_1", "watch"}:
        raise ValueError(
            "standing watch priority must be tier_1 or watch"
        )
    if identity_status not in {"verified", "pending"}:
        raise ValueError(
            "standing watch identity status must be verified or pending"
        )
    normalized_hosts = tuple(sorted(
        (normalize_trusted_organizer_domain(value)
         for value in official_hosts),
        key=lambda value: (value.casefold(), value),
    ))
    aliases = _normalized_values((canonical_name, *query_aliases))
    return {
        "id": target_id,
        "kind": kind,
        "canonical_name": canonical_name,
        "priority": priority,
        "role": role,
        "identity_status": identity_status,
        "official_hosts": normalized_hosts,
        "query_aliases": aliases,
    }


def _watch_source_classes(
    kind: str,
    role: str,
    identity_status: str,
) -> tuple[EventSourceClass, ...]:
    if role == "discovery_only" or identity_status == "pending":
        return (EventSourceClass.DISCOVERY_INDEX,)
    if kind == "organizer":
        return (
            EventSourceClass.OFFICIAL_ORGANIZER,
            EventSourceClass.OFFICIAL_AGENDA_ATTENDANCE,
            EventSourceClass.OFFICIAL_REGISTRATION_VENUE,
        )
    return CLIENT_EVENT_SOURCE_CLASSES


def _watch_query_text(
    *,
    target: dict[str, object],
    source_class: EventSourceClass,
    nonrequired_reason: Optional[str],
    window_start: date,
    flagship_through: date,
) -> str:
    canonical_name = str(target["canonical_name"])
    if nonrequired_reason == "scope_excluded":
        return (
            f'Standing watch target "{canonical_name}" is not applicable to '
            f"the approved client frame; {source_class.value.replace('_', ' ')} "
            "query is scope excluded."
        )
    aliases = tuple(target["query_aliases"])
    terms = " OR ".join(f'"{value}"' for value in aliases)
    hosts = tuple(target["official_hosts"])
    host_label = (
        "discovery hosts"
        if source_class is EventSourceClass.DISCOVERY_INDEX
        else "official hosts"
    )
    host_hint = f" {host_label}: " + ", ".join(hosts) if hosts else ""
    return (
        f"{_SOURCE_INSTRUCTIONS[source_class]} for standing watch target "
        f"{terms}; event window {window_start.isoformat()} through "
        f"{flagship_through.isoformat()}.{host_hint}"
    )


def build_event_query_manifest(
    frame: EventResearchFrame,
    as_of: datetime,
    *,
    watch_selection: Optional[object] = None,
    watch_universe_digest: Optional[str] = None,
) -> EventQueryManifest:
    """Build the base census and optional target-level standing watch."""

    _require_aware(as_of, "event query manifest as_of")
    window_start = as_of.date()
    confirmed_through = _add_months(window_start, 12)
    flagship_through = _add_months(window_start, 18)
    frame_domains = tuple(sorted(
        (domain.casefold() for domain in frame.trusted_organizer_domains),
        key=lambda value: (value.casefold(), value),
    ))
    queries: list[EventQuerySpec] = []
    for family in CLIENT_EVENT_QUERY_FAMILIES:
        values = _normalized_values(frame.values_for(family))
        required = bool(values)
        nonrequired_reason = None if required else "empty_client_frame"
        for source_class in CLIENT_EVENT_SOURCE_CLASSES:
            text = _query_text(
                family=family,
                source_class=source_class,
                values=values,
                trusted_domains=frame_domains,
                window_start=window_start,
                flagship_through=flagship_through,
            )
            queries.append(EventQuerySpec(
                query_id=_query_id(
                    family,
                    source_class,
                    text,
                    required,
                    nonrequired_reason=nonrequired_reason,
                ),
                query_family=family,
                source_class=source_class,
                query_text=text,
                required=required,
                nonrequired_reason=nonrequired_reason,
            ))

    watch_target_ids: tuple[str, ...] = ()
    applicable_watch_target_ids: tuple[str, ...] = ()
    search_due_watch_target_ids: tuple[str, ...] = ()
    cadence_deferred_watch_target_ids: tuple[str, ...] = ()
    scope_excluded_watch_target_ids: tuple[str, ...] = ()
    watch_hosts: tuple[str, ...] = ()
    if bool(watch_selection) != bool(watch_universe_digest):
        raise ValueError(
            "standing watch selection and universe digest must be supplied together"
        )
    if watch_selection is not None:
        _require_sha256(
            str(watch_universe_digest),
            "event watch universe SHA-256",
        )
        try:
            applicable_raw = tuple(getattr(watch_selection, "applicable"))
            due_raw = tuple(getattr(watch_selection, "search_due"))
            deferred_raw = tuple(
                getattr(watch_selection, "cadence_deferred")
            )
            excluded_raw = tuple(getattr(watch_selection, "scope_excluded"))
        except (AttributeError, TypeError) as exc:
            raise ValueError("standing watch selection is invalid") from exc
        applicable_rows = tuple(
            _watch_target_values(target) for target in applicable_raw
        )
        excluded_rows = tuple(
            _watch_target_values(target) for target in excluded_raw
        )
        applicable_ids = {str(row["id"]) for row in applicable_rows}
        due_ids = {
            str(_watch_target_values(target)["id"]) for target in due_raw
        }
        deferred_ids = {
            str(_watch_target_values(target)["id"]) for target in deferred_raw
        }
        excluded_ids = {str(row["id"]) for row in excluded_rows}
        if applicable_ids & excluded_ids:
            raise ValueError("standing watch applicability overlaps")
        if deferred_ids:
            raise ValueError(
                "applicable standing-watch targets cannot be cadence deferred"
            )
        if due_ids != applicable_ids:
            raise ValueError(
                "every applicable standing-watch target must be search due"
            )
        rows = tuple(sorted(
            (*applicable_rows, *excluded_rows),
            key=lambda row: (
                {"tier_1": 0, "watch": 1}[str(row["priority"])],
                str(row["kind"]),
                str(row["id"]),
            ),
        ))
        _require_unique(
            (str(row["id"]) for row in rows),
            "standing watch target IDs",
        )
        watch_target_ids = tuple(str(row["id"]) for row in rows)
        applicable_watch_target_ids = tuple(
            target_id for target_id in watch_target_ids
            if target_id in applicable_ids
        )
        search_due_watch_target_ids = tuple(
            target_id for target_id in watch_target_ids
            if target_id in due_ids
        )
        cadence_deferred_watch_target_ids = tuple(
            target_id for target_id in watch_target_ids
            if target_id in deferred_ids
        )
        scope_excluded_watch_target_ids = tuple(
            target_id for target_id in watch_target_ids
            if target_id in excluded_ids
        )
        watch_hosts = tuple(sorted({
            host
            for row in rows
            if str(row["id"]) in applicable_ids
            if row["role"] != "discovery_only"
            if row["identity_status"] == "verified"
            for host in tuple(row["official_hosts"])
        }))
        for row in rows:
            target_id = str(row["id"])
            required = target_id in due_ids
            nonrequired_reason = None if required else "scope_excluded"
            family = (
                EventQueryFamily.STANDING_ORGANIZER
                if row["kind"] == "organizer"
                else EventQueryFamily.STANDING_EVENT_SERIES
            )
            for source_class in _watch_source_classes(
                str(row["kind"]),
                str(row["role"]),
                str(row["identity_status"]),
            ):
                text = _watch_query_text(
                    target=row,
                    source_class=source_class,
                    nonrequired_reason=nonrequired_reason,
                    window_start=window_start,
                    flagship_through=flagship_through,
                )
                metadata = {
                    "watch_target_id": target_id,
                    "watch_target_kind": str(row["kind"]),
                    "watch_priority": str(row["priority"]),
                    "source_role": str(row["role"]),
                    "watch_identity_status": str(row["identity_status"]),
                    "official_hosts": tuple(row["official_hosts"]),
                    "nonrequired_reason": nonrequired_reason,
                }
                queries.append(EventQuerySpec(
                    query_id=_query_id(
                        family,
                        source_class,
                        text,
                        required,
                        **metadata,
                    ),
                    query_family=family,
                    source_class=source_class,
                    query_text=text,
                    required=required,
                    **metadata,
                ))
    trusted_domains = tuple(sorted(set(frame_domains) | set(watch_hosts)))
    query_tuple = tuple(queries)
    common = dict(
        binding=frame.binding,
        frame_revision_sha256=frame.revision_sha256,
        window_start=window_start,
        confirmed_event_through=confirmed_through,
        flagship_event_through=flagship_through,
        trusted_organizer_domains=trusted_domains,
        watch_universe_sha256=watch_universe_digest,
        watch_target_ids=watch_target_ids,
        applicable_watch_target_ids=applicable_watch_target_ids,
        search_due_watch_target_ids=search_due_watch_target_ids,
        cadence_deferred_watch_target_ids=(
            cadence_deferred_watch_target_ids
        ),
        scope_excluded_watch_target_ids=scope_excluded_watch_target_ids,
        queries=query_tuple,
    )
    return EventQueryManifest(
        manifest_id=_digest(_manifest_basis(
            **common,
            purpose="event-query-manifest",
        )),
        cache_key=_digest(_manifest_basis(
            **common,
            purpose="event-query-cache",
        )),
        version=MANIFEST_VERSION,
        **common,
    )


def _lead_sort_key(lead: EventLead) -> tuple[str, ...]:
    return (
        lead.title.casefold(),
        str(lead.url).casefold(),
        lead.claimed_date or "",
        lead.claimed_location or "",
        lead.claimed_organizer or "",
        lead.lead_id,
    )


def _validated_query_leads(
    query: EventQuerySpec,
    raw: Iterable[Union[EventLead, Mapping[str, object]]],
    attempted_at: datetime,
) -> tuple[EventLead, ...]:
    if isinstance(raw, (str, bytes, Mapping)):
        raise TypeError("event searcher must return an iterable of lead rows")
    by_id: dict[str, EventLead] = {}
    for value in raw:
        lead = value if isinstance(value, EventLead) \
            else EventLead.model_validate(value)
        if lead.query_id != query.query_id:
            raise ValueError("event searcher returned a lead for another query")
        if lead.source_class != query.source_class:
            raise ValueError("event searcher returned the wrong source class")
        if lead.discovered_at > attempted_at:
            raise ValueError(
                "event lead discovery timestamp is after its query attempt"
            )
        if lead.lead_id in by_id:
            raise ValueError("event searcher returned duplicate lead IDs")
        by_id[lead.lead_id] = lead
    return tuple(sorted(by_id.values(), key=_lead_sort_key))


def run_event_discovery(
    manifest: EventQueryManifest,
    searcher: Union[
        EventDiscoverySearcher,
        Callable[[EventQuerySpec], Union[
            EventSearchResponse,
            Iterable[Union[EventLead, Mapping[str, object]]],
        ]],
    ],
    attempted_at: datetime,
) -> EventDiscoveryResult:
    """Run an injected discovery provider and record the complete census.

    A successful zero-result query is ``RETURNED`` with zero records.  A
    provider exception or malformed response is ``FAILED`` with a generic,
    public-safe explanation; exception text never crosses this boundary.
    Queries whose approved family has no inputs or whose watch target is out of
    scope are marked ``SCOPE_EXCLUDED``.  Every applicable standing-watch
    target remains required on every run; priority changes search order only.
    """

    _require_aware(attempted_at, "event discovery attempted_at")
    all_leads: list[EventLead] = []
    attempts: list[EventQueryAttempt] = []
    for query in manifest.queries:
        if not query.required:
            if query.nonrequired_reason == "scope_excluded" and (
                query.watch_target_id is not None
            ):
                state = CoverageState.SCOPE_EXCLUDED
                detail = (
                    "This standing watch target is outside the approved "
                    "client frame."
                )
            else:
                state = CoverageState.SCOPE_EXCLUDED
                detail = (
                    "No approved inputs were supplied for this query family."
                )
            attempts.append(EventQueryAttempt(
                query_id=query.query_id,
                state=state,
                attempted_at=None,
                records_returned=0,
                lead_ids=(),
                public_detail=detail,
            ))
            continue
        try:
            raw = searcher(query)
        except Exception:  # noqa: BLE001 - provider detail stays internal
            attempts.append(EventQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.FAILED,
                attempted_at=attempted_at,
                records_returned=0,
                lead_ids=(),
                public_detail=(
                    "The discovery provider did not return a usable response "
                    "for this query."
                ),
            ))
            continue
        # Provider failures are sanitized above; malformed provider *data* is
        # a contract violation and must fail closed so it cannot masquerade as
        # a transient coverage miss.
        if isinstance(raw, EventSearchResponse):
            leads = _validated_query_leads(
                query,
                raw.leads,
                attempted_at,
            )
            state = raw.state
            records_returned = raw.records_returned
            detail = raw.public_detail
        else:
            if raw is None:
                raise TypeError("event searcher returned no iterable")
            leads = _validated_query_leads(query, raw, attempted_at)
            state = CoverageState.RETURNED
            records_returned = len(leads)
            detail = (
                "Discovery completed with no leads."
                if not leads
                else f"Discovery returned {len(leads)} unverified lead(s)."
            )
        all_leads.extend(leads)
        attempts.append(EventQueryAttempt(
            query_id=query.query_id,
            state=state,
            attempted_at=(
                None if state is CoverageState.NOT_RUN else attempted_at
            ),
            records_returned=records_returned,
            lead_ids=tuple(lead.lead_id for lead in leads),
            public_detail=detail,
        ))
    return EventDiscoveryResult(
        manifest=manifest,
        attempted_at=attempted_at,
        leads=tuple(all_leads),
        attempts=tuple(attempts),
    )


__all__ = (
    "CLIENT_EVENT_QUERY_FAMILIES",
    "CLIENT_EVENT_SOURCE_CLASSES",
    "MANIFEST_VERSION",
    "EventDiscoveryResult",
    "EventDiscoverySearcher",
    "EventLead",
    "EventSearchResponse",
    "EventQueryAttempt",
    "EventQueryFamily",
    "EventQueryManifest",
    "EventQuerySpec",
    "EventResearchFrame",
    "EventSourceClass",
    "build_event_query_manifest",
    "normalize_trusted_organizer_domain",
    "run_event_discovery",
)
