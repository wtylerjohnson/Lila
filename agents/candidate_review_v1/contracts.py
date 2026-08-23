"""Frozen Candidate Review v1 product and evidence contracts.

This module is deliberately additive.  It does not inherit the legacy pursuit
document because that document carries bid/no-bid, dossier, and pipeline
semantics that are outside Candidate Review.  These models define the sole
input boundary for the future renderer and the pure presentation projection.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import re
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)


SCHEMA_VERSION = "candidate_review_v1.document.v1"
CURRENT_WATCH_MAX_AGE = timedelta(days=7)
NEAR_TERM_WATCH_MAX_AGE = timedelta(hours=24)
NEAR_TERM_WATCH_DAYS = 30


class _FrozenContract(BaseModel):
    """Immutable, strict boundary shared by every Candidate Review model."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLIENT_ID_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


def _validate_hash(value: str, label: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _validate_watch_freshness(
    *,
    label: str,
    last_checked_at: datetime,
    as_of: datetime,
    current: bool,
    milestone_dates: tuple[date, ...] = (),
) -> None:
    """Enforce the locked cache and near-term recheck policy."""

    if not current:
        return
    age = as_of - last_checked_at
    if age > CURRENT_WATCH_MAX_AGE:
        raise ValueError(f"{label} must be checked within seven days")
    near_term_through = as_of.date() + timedelta(days=NEAR_TERM_WATCH_DAYS)
    if any(
        as_of.date() <= milestone <= near_term_through
        for milestone in milestone_dates
    ) and age > NEAR_TERM_WATCH_MAX_AGE:
        raise ValueError(
            f"near-term {label} must be rechecked during the run"
        )


def validate_event_watch_freshness(
    event: "EventRecord",
    *,
    as_of: datetime,
) -> None:
    """Require current published event editions to meet watch freshness."""

    _validate_watch_freshness(
        label="event edition",
        last_checked_at=event.last_checked_at,
        as_of=as_of,
        current=event.active and event.kind in {
            EventKind.CONFERENCE,
            EventKind.INDUSTRY_EVENT,
        },
        milestone_dates=(
            (event.timing.sort_date,) if event.timing is not None else ()
        ),
    )


def _client_slug(value: str) -> str:
    from tools.slug import client_slug
    return client_slug(value)


FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"\bpriority federal routes\b", re.I),
    re.compile(r"\baccount ownership(?: to validate)?\b", re.I),
    re.compile(r"\bagency targets and contacts(?: to validate)?\b", re.I),
    re.compile(r"\bpursuit dossier\b", re.I),
    re.compile(r"\bwin probability\b", re.I),
    re.compile(r"\bpipeline value\b", re.I),
    re.compile(r"\bautomatically disqualif(?:y|ied|ication)\b", re.I),
    re.compile(r"\brecommend(?:ed|ation)?\s+(?:to\s+)?(?:pursue|no[- ]bid)\b", re.I),
    re.compile(r"\bmust\s+(?:pursue|no[- ]bid)\b", re.I),
    re.compile(r"\bdisqualif(?:y|ied|ication)\b", re.I),
    re.compile(r"\bno[- ]bid\b", re.I),
    re.compile(r"\bpursue\b", re.I),
    re.compile(r"^\s*(?:faces|leadership|decision makers|key people)\s*$", re.I),
)


def assert_candidate_review_language(*values: str) -> None:
    """Reject legacy labels and automated business dispositions in copy.

    Raw evidence excerpts are intentionally not passed through this function;
    source records must remain verbatim even when they contain vendor language.
    """

    for value in values:
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(value or ""):
                raise ValueError(
                    f"Candidate Review copy contains forbidden language: "
                    f"{pattern.pattern}"
                )


class ArtifactBinding(_FrozenContract):
    """Exact approved client, run, scope, profile, and evidence universe."""

    client_id: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_designator: str = Field(min_length=1)
    scope_sha256: str
    profile_sha256: str
    evidence_snapshot_sha256: str

    @model_validator(mode="after")
    def _binding_is_canonical(self) -> "ArtifactBinding":
        if _CLIENT_ID_RE.fullmatch(self.client_id) is None:
            raise ValueError("client_id must be a canonical lowercase slug")
        from tools.slug import legacy_hyphen_client_id
        if self.client_id not in (_client_slug(self.client_name),
                                  legacy_hyphen_client_id(self.client_name)):
            # Canonical binds new artifacts; the legacy hyphen id stays
            # valid so pre-consolidation generations replay (read-only
            # tolerance, never a new mint).
            raise ValueError("client name does not match canonical client_id")
        for label, value in (
            ("scope_sha256", self.scope_sha256),
            ("profile_sha256", self.profile_sha256),
            ("evidence_snapshot_sha256", self.evidence_snapshot_sha256),
        ):
            _validate_hash(value, label)
        return self


class SourceTier(str, Enum):
    NOTICE = "notice"
    PROGRAM = "program"
    MARKET = "market"
    DISCOVERY = "discovery"


class EvidenceKind(str, Enum):
    NOTICE = "notice"
    AGENCY_FORECAST = "agency_forecast"
    AWARD = "award"
    BUDGET = "budget"
    LEGISLATION = "legislation"
    REGULATION = "regulation"
    AGENCY_ANNOUNCEMENT = "agency_announcement"
    WATCHDOG = "watchdog"
    SUBAWARD = "subaward"
    VEHICLE = "vehicle"
    OFFICIAL_EVENT = "official_event"
    ORGANIZER_EVENT = "organizer_event"
    NEWS = "news"
    WEB_DISCOVERY = "web_discovery"


# This is the single candidate-anchor policy used by both the build engine and
# the serialized document boundary.  Context-only records can support a buying
# hypothesis, but cannot become a Candidate opportunities card on their own.
CANDIDATE_ANCHOR_KINDS = frozenset({
    EvidenceKind.NOTICE,
    EvidenceKind.AGENCY_FORECAST,
    EvidenceKind.AWARD,
    EvidenceKind.BUDGET,
    EvidenceKind.AGENCY_ANNOUNCEMENT,
    EvidenceKind.WATCHDOG,
    EvidenceKind.SUBAWARD,
})


class EvidenceUse(str, Enum):
    REQUIREMENT = "requirement"
    TIMING = "timing"
    FUNDING = "funding"
    BUYER = "buyer"
    CAPABILITY = "capability"
    ACCESS = "access"
    INCUMBENT = "incumbent"
    COUNTEREVIDENCE = "counterevidence"
    EVENT = "event"


class NoticeStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    AWARDED = "awarded"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class NoticeRole(str, Enum):
    """The acquisition role of an official SAM notice.

    Vehicle establishment, on-ramp, and administration notices are actionable
    access signals, but they are not end-user demand and therefore never create
    a Candidate opportunities card.
    """

    END_USER_REQUIREMENT = "end_user_requirement"
    TASK_ORDER_REQUIREMENT = "task_order_requirement"
    VEHICLE_ESTABLISHMENT = "vehicle_establishment"
    VEHICLE_ON_RAMP = "vehicle_on_ramp"
    VEHICLE_ADMINISTRATION = "vehicle_administration"


CURRENT_CANDIDATE_NOTICE_ROLES = frozenset({
    NoticeRole.END_USER_REQUIREMENT,
    NoticeRole.TASK_ORDER_REQUIREMENT,
})


class EvidenceAssertion(str, Enum):
    EXPLICIT_RECOMPETE = "explicit_recompete"
    OFFICIAL_EVENT = "official_event"
    AGENCY_ATTENDANCE = "agency_attendance"
    AWARD_PERIOD_END = "award_period_end"
    EVENT_FLAGSHIP = "event_flagship"
    EVENT_CANCELLED = "event_cancelled"
    EVENT_POSTPONED = "event_postponed"
    EVENT_DATE_TBD = "event_date_tbd"
    VEHICLE_IDENTITY = "vehicle_identity"
    PARENT_IDV_RELATIONSHIP = "parent_idv_relationship"
    VEHICLE_ON_RAMP = "vehicle_on_ramp"
    VEHICLE_ON_RAMP_OPEN = "vehicle_on_ramp_open"
    VEHICLE_ON_RAMP_CLOSE = "vehicle_on_ramp_close"
    VEHICLE_ORDERING_PERIOD = "vehicle_ordering_period"
    VEHICLE_OPTION_END = "vehicle_option_end"
    VEHICLE_DIRECT_HOLDER = "vehicle_direct_holder"
    VEHICLE_TEAMING_REQUIRED = "vehicle_teaming_required"
    VEHICLE_CHANNEL_OR_RESELLER = "vehicle_channel_or_reseller"
    VEHICLE_CLASS = "vehicle_class"
    VEHICLE_MANAGEMENT = "vehicle_management"
    VEHICLE_SCOPE = "vehicle_scope"
    VEHICLE_ELIGIBILITY = "vehicle_eligibility"
    VEHICLE_HOLDER = "vehicle_holder"
    VEHICLE_PARTNER_ROUTE = "vehicle_partner_route"
    VEHICLE_ORDERING_STATUS = "vehicle_ordering_status"
    VEHICLE_SOURCE_DATA_AS_OF = "vehicle_source_data_as_of"


class EvidenceAssertionSpan(_FrozenContract):
    assertion: EvidenceAssertion
    quote: str = Field(min_length=1)


class SourceIdentity(_FrozenContract):
    source_system: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    revision_id: Optional[str] = None

    @property
    def canonical_key(self) -> str:
        base = f"{self.source_system.casefold()}:{self.record_id.casefold()}"
        return f"{base}:{self.revision_id.casefold()}" if self.revision_id else base


class EvidenceRecord(_FrozenContract):
    """One immutable, client/run/scope-bound source observation."""

    evidence_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_sha256: str
    source_identity: SourceIdentity
    source_tier: SourceTier
    source_kind: EvidenceKind
    source_name: str = Field(min_length=1)
    source_url: HttpUrl
    retrieved_at: datetime
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    published_date: Optional[date] = None
    effective_date: Optional[date] = None
    record_sha256: Optional[str] = None
    official_source: bool = False
    primary_source: bool = False
    supports: tuple[EvidenceUse, ...] = Field(default_factory=tuple)
    notice_status: Optional[NoticeStatus] = None
    notice_role: Optional[NoticeRole] = None
    verified_at: Optional[datetime] = None
    assertion_spans: tuple[EvidenceAssertionSpan, ...] = Field(
        default_factory=tuple)
    issuing_office: Optional[str] = None
    solicitation_number: Optional[str] = None

    @model_validator(mode="after")
    def _source_boundary_is_explicit(self) -> "EvidenceRecord":
        _validate_hash(self.scope_sha256, "evidence scope_sha256")
        _require_aware(self.retrieved_at, "evidence retrieved_at")
        parsed = urlparse(str(self.source_url))
        if parsed.scheme != "https":
            raise ValueError("evidence source_url must use HTTPS")
        host = (parsed.hostname or "").lower()
        if self.source_kind == EvidenceKind.NOTICE:
            if host != "sam.gov" and not host.endswith(".sam.gov"):
                raise ValueError("notice evidence must resolve to SAM.gov")
            if self.source_identity.source_system.casefold() != "sam.gov":
                raise ValueError("notice source identity must be SAM.gov")
            if self.source_tier != SourceTier.NOTICE:
                raise ValueError("notice evidence must use the notice source tier")
            if not self.official_source or not self.primary_source:
                raise ValueError(
                    "notice evidence must be official primary-source evidence"
                )
            if self.notice_status is None or self.verified_at is None:
                raise ValueError(
                    "notice evidence requires explicit status and verification time"
                )
            if self.notice_role is None:
                raise ValueError("notice evidence requires an acquisition role")
            _require_aware(self.verified_at, "notice verified_at")
            if self.verified_at < self.retrieved_at:
                raise ValueError("notice verification cannot predate retrieval")
            notice_key = self.source_identity.record_id.casefold()
            if notice_key not in str(self.source_url).casefold():
                raise ValueError(
                    "notice source identity must appear in its SAM.gov URL"
                )
            if bool(self.issuing_office) != bool(self.solicitation_number):
                raise ValueError(
                    "notice procurement identity requires both issuing office "
                    "and solicitation number"
                )
        elif self.notice_status is not None or self.notice_role is not None:
            raise ValueError(
                "notice_status and notice_role are only valid for notice evidence"
            )
        elif self.verified_at is not None:
            raise ValueError("verified_at is only valid for notice evidence")
        elif self.issuing_office is not None or self.solicitation_number is not None:
            raise ValueError("procurement family fields are only valid for notices")
        elif self.source_tier == SourceTier.NOTICE:
            raise ValueError("notice source tier is reserved for notice evidence")
        if self.source_kind in (EvidenceKind.NEWS, EvidenceKind.WEB_DISCOVERY):
            if self.primary_source:
                raise ValueError("discovery/news evidence cannot be primary evidence")
        if self.record_sha256 is not None:
            _validate_hash(self.record_sha256, "record_sha256")
        _unique(tuple(item.value for item in self.supports), "evidence supports")
        assertion_values = tuple(item.assertion.value for item in self.assertion_spans)
        _unique(assertion_values, "evidence assertions")
        if any(item.quote not in self.excerpt for item in self.assertion_spans):
            raise ValueError("evidence assertion quote must be an exact excerpt span")
        if self.assertion_spans and (not self.official_source or not self.primary_source):
            raise ValueError("evidence assertions require official primary evidence")
        return self

    @property
    def can_anchor_candidate(self) -> bool:
        if self.source_kind not in CANDIDATE_ANCHOR_KINDS:
            return False
        if self.source_kind == EvidenceKind.NOTICE:
            return self.notice_role in CURRENT_CANDIDATE_NOTICE_ROLES
        return True

    @property
    def confirms_open_notice(self) -> bool:
        return (
            self.source_kind == EvidenceKind.NOTICE
            and self.official_source
            and self.primary_source
            and self.notice_status == NoticeStatus.ACTIVE
            and self.notice_role in CURRENT_CANDIDATE_NOTICE_ROLES
        )

    def has_assertion(self, assertion: EvidenceAssertion) -> bool:
        return any(item.assertion == assertion for item in self.assertion_spans)


class DateKind(str, Enum):
    RESPONSE_DEADLINE = "response_deadline"
    QA_DEADLINE = "qa_deadline"
    SITE_VISIT = "site_visit"
    INDUSTRY_DAY = "industry_day"
    FORECAST_SOLICITATION = "forecast_solicitation"
    FORECAST_AWARD = "forecast_award"
    PROGRAM_DEADLINE = "program_deadline"
    CONFIRMED_RECOMPETE = "confirmed_recompete"
    AWARD_END_RESEARCH_CLOCK = "award_end_research_clock"
    BUDGET_MILESTONE = "budget_milestone"
    EVENT_START = "event_start"
    EVENT_END = "event_end"
    MONITOR_DATE = "monitor_date"
    ON_RAMP_OPEN = "on_ramp_open"
    ON_RAMP_CLOSE = "on_ramp_close"
    ORDERING_PERIOD_END = "ordering_period_end"
    VEHICLE_OPTION_END_RESEARCH_CLOCK = "vehicle_option_end_research_clock"


VEHICLE_MILESTONE_DATE_KINDS = frozenset({
    DateKind.ON_RAMP_OPEN,
    DateKind.ON_RAMP_CLOSE,
    DateKind.ORDERING_PERIOD_END,
    DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK,
})


class DateStatus(str, Enum):
    CONFIRMED = "confirmed"
    ANTICIPATED = "anticipated"
    ESTIMATED = "estimated"
    RESEARCH_CLOCK = "research_clock"
    MONITOR = "monitor"


class DatePrecision(str, Enum):
    DAY = "day"
    MONTH = "month"
    QUARTER = "quarter"
    FISCAL_YEAR = "fiscal_year"
    RANGE = "range"


def _date_source_lexemes(day: date) -> tuple[str, ...]:
    month = day.strftime("%B").casefold()
    abbreviated = day.strftime("%b").casefold()
    return (
        day.isoformat(),
        f"{month} {day.day}, {day.year}",
        f"{abbreviated} {day.day}, {day.year}",
        f"{day.month}/{day.day}/{day.year}",
        f"{day.month:02d}/{day.day:02d}/{day.year}",
        f"{day.year}/{day.month:02d}/{day.day:02d}",
    )


def _source_text_names_day(source_text: str, day: date) -> bool:
    normalized = " ".join(source_text.casefold().split())
    return any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(lexeme)}(?![a-z0-9])",
            normalized,
        ) is not None
        for lexeme in _date_source_lexemes(day)
    )


def _source_text_names_identifier(source_text: str, identifier: str) -> bool:
    """Match one exact procurement identifier, not a substring lookalike."""

    return re.search(
        rf"(?<![A-Za-z0-9]){re.escape(identifier)}(?![A-Za-z0-9])",
        source_text,
        re.IGNORECASE,
    ) is not None


def _source_text_names_range(
    source_text: str,
    start: date,
    end: date,
) -> bool:
    if _source_text_names_day(source_text, start) \
            and _source_text_names_day(source_text, end):
        return True
    if start.year == end.year and start.month == end.month:
        month_names = (start.strftime("%B"), start.strftime("%b"))
        return any(re.search(
            rf"\b{re.escape(month)}\s+{start.day}\s*"
            rf"(?:-|–|—|to|through)\s*{end.day}\s*,?\s*{start.year}\b",
            source_text,
            re.IGNORECASE,
        ) for month in month_names)
    if start.year == end.year:
        start_months = (start.strftime("%B"), start.strftime("%b"))
        end_months = (end.strftime("%B"), end.strftime("%b"))
        return any(re.search(
            rf"\b{re.escape(start_month)}\s+{start.day}\s*"
            rf"(?:-|–|—|to|through)\s*"
            rf"{re.escape(end_month)}\s+{end.day}\s*,?\s*{start.year}\b",
            source_text,
            re.IGNORECASE,
        ) for start_month in start_months for end_month in end_months)
    return False


def _source_text_names_month(source_text: str, sort_date: date) -> bool:
    normalized = " ".join(source_text.casefold().split())
    month = sort_date.strftime("%B").casefold()
    abbreviated = sort_date.strftime("%b").casefold()
    return any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])",
            normalized,
        ) is not None
        for value in (
            f"{month} {sort_date.year}",
            f"{abbreviated} {sort_date.year}",
            f"{sort_date.year}-{sort_date.month:02d}",
            f"{sort_date.month:02d}/{sort_date.year}",
        )
    )


def _quarter_source_start(source_text: str) -> Optional[date]:
    normalized = " ".join(source_text.casefold().split())
    match = re.search(
        r"\bfy\s*([0-9]{4})\s+q([1-4])\b|"
        r"\bq([1-4])\s+fy\s*([0-9]{4})\b",
        normalized,
    )
    if match:
        fiscal_year = int(match.group(1) or match.group(4))
        quarter = int(match.group(2) or match.group(3))
        starts = (
            date(fiscal_year - 1, 10, 1),
            date(fiscal_year, 1, 1),
            date(fiscal_year, 4, 1),
            date(fiscal_year, 7, 1),
        )
        return starts[quarter - 1]
    match = re.search(
        r"\b([0-9]{4})\s+q([1-4])\b|"
        r"\bq([1-4])\s+([0-9]{4})\b",
        normalized,
    )
    if not match:
        return None
    year = int(match.group(1) or match.group(4))
    quarter = int(match.group(2) or match.group(3))
    return date(year, 1 + (quarter - 1) * 3, 1)


def _fiscal_year_source_start(source_text: str) -> Optional[date]:
    normalized = " ".join(source_text.casefold().split())
    match = re.search(
        r"\b(?:fy\s*|fiscal\s+year\s+)([0-9]{4})\b",
        normalized,
    )
    if not match:
        return None
    fiscal_year = int(match.group(1))
    return date(fiscal_year - 1, 10, 1)


class DateValue(_FrozenContract):
    date_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: DateKind
    status: DateStatus
    precision: DatePrecision
    source_text: str = Field(min_length=1)
    sort_date: date
    sort_datetime: Optional[datetime] = None
    start: Optional[date] = None
    end: Optional[date] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _date_semantics_do_not_drift(self) -> "DateValue":
        if self.sort_datetime is not None:
            _require_aware(self.sort_datetime, "date sort_datetime")
            if self.sort_datetime.date() != self.sort_date:
                raise ValueError("date sort_datetime must fall on sort_date")
            if self.precision not in (DatePrecision.DAY, DatePrecision.RANGE):
                raise ValueError(
                    "only day or range precision can carry an asserted time"
                )
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("date end precedes start")
        _unique(self.evidence_ids, "date evidence IDs")
        research_clock_kinds = {
            DateKind.AWARD_END_RESEARCH_CLOCK,
            DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK,
        }
        if self.kind in research_clock_kinds \
                and self.status != DateStatus.RESEARCH_CLOCK:
            raise ValueError("award or vehicle option end can only be a research clock")
        if self.kind == DateKind.ORDERING_PERIOD_END \
                and self.status != DateStatus.MONITOR:
            raise ValueError("ordering-period end can only be a monitor date")
        if self.kind == DateKind.CONFIRMED_RECOMPETE \
                and self.status != DateStatus.CONFIRMED:
            raise ValueError("confirmed recompete requires confirmed status")
        if self.kind in (
            DateKind.FORECAST_SOLICITATION,
            DateKind.FORECAST_AWARD,
        ) and self.status == DateStatus.CONFIRMED:
            raise ValueError("forecast dates cannot be represented as confirmed")
        if self.precision == DatePrecision.DAY:
            if self.start is None or self.end is not None or self.sort_date != self.start:
                raise ValueError(
                    "day precision requires one asserted start equal to sort_date"
                )
            if not _source_text_names_day(self.source_text, self.start):
                raise ValueError(
                    "day precision structured date is absent from source_text"
                )
        elif self.precision == DatePrecision.RANGE:
            if self.start is None or self.end is None or self.sort_date != self.start:
                raise ValueError(
                    "range precision requires asserted start/end and start sort_date"
                )
            if not _source_text_names_range(
                self.source_text,
                self.start,
                self.end,
            ):
                raise ValueError(
                    "range precision structured dates are absent from source_text"
                )
        else:
            if self.start is not None or self.end is not None:
                raise ValueError(
                    "month, quarter, and fiscal-year dates use source_text and "
                    "sort_date, not a synthetic asserted day"
                )
            if self.precision == DatePrecision.MONTH:
                if self.sort_date.day != 1 or not _source_text_names_month(
                    self.source_text,
                    self.sort_date,
                ):
                    raise ValueError(
                        "month precision sort_date differs from source_text"
                    )
            elif self.precision == DatePrecision.QUARTER:
                if _quarter_source_start(self.source_text) != self.sort_date:
                    raise ValueError(
                        "quarter precision sort_date differs from source_text"
                    )
            elif self.precision == DatePrecision.FISCAL_YEAR:
                if _fiscal_year_source_start(self.source_text) != self.sort_date:
                    raise ValueError(
                        "fiscal-year precision sort_date differs from source_text"
                    )
        return self


def evidence_supports_date_value(
    value: DateValue,
    records: tuple[EvidenceRecord, ...],
) -> bool:
    """Return whether one record proves both the date and its semantic role.

    A date lexeme elsewhere on a page is not sufficient.  For example, a
    publication date cannot be promoted to a response deadline merely because
    both values occur in the same notice.  The matching date must occur in a
    clause that names the typed role, or in the exact assertion span used for
    recompete, award-end, or event proof.
    """

    return any(_record_supports_date_value(value, record) for record in records)


def _text_names_date_value(value: DateValue, text: str) -> bool:
    if value.precision == DatePrecision.DAY:
        return value.start is not None \
            and _source_text_names_day(text, value.start)
    if value.precision == DatePrecision.RANGE:
        return value.start is not None and value.end is not None \
            and _source_text_names_range(text, value.start, value.end)
    if value.precision == DatePrecision.MONTH:
        return _source_text_names_month(text, value.sort_date)
    if value.precision == DatePrecision.QUARTER:
        return _quarter_source_start(text) == value.sort_date
    if value.precision == DatePrecision.FISCAL_YEAR:
        return _fiscal_year_source_start(text) == value.sort_date
    return False


def _record_supports_date_value(
    value: DateValue,
    record: EvidenceRecord,
) -> bool:
    assertion_roles = {
        DateKind.CONFIRMED_RECOMPETE: EvidenceAssertion.EXPLICIT_RECOMPETE,
        DateKind.AWARD_END_RESEARCH_CLOCK: EvidenceAssertion.AWARD_PERIOD_END,
        DateKind.EVENT_START: EvidenceAssertion.OFFICIAL_EVENT,
        DateKind.EVENT_END: EvidenceAssertion.OFFICIAL_EVENT,
        DateKind.ON_RAMP_OPEN: EvidenceAssertion.VEHICLE_ON_RAMP_OPEN,
        DateKind.ON_RAMP_CLOSE: EvidenceAssertion.VEHICLE_ON_RAMP_CLOSE,
        DateKind.ORDERING_PERIOD_END:
            EvidenceAssertion.VEHICLE_ORDERING_PERIOD,
        DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK:
            EvidenceAssertion.VEHICLE_OPTION_END,
    }
    required_assertion = assertion_roles.get(value.kind)
    if required_assertion is not None:
        return any(
            span.assertion == required_assertion
            and _text_names_date_value(value, span.quote)
            for span in record.assertion_spans
        )

    fragments: list[str] = []
    for field in (record.title, record.excerpt):
        fragments.extend(
            fragment.strip()
            for fragment in re.split(
                r"(?<=[.!?;])\s+|[()\r\n]+|"
                r",\s+(?![0-9]{4}\b)|"
                r"\s+\b(?:and|but|while|whereas)\b\s+",
                field,
                flags=re.IGNORECASE,
            )
            if fragment.strip()
        )
    return any(
        _text_names_date_value(value, fragment)
        and _date_role_binds_to_value(value, fragment)
        for fragment in fragments
    )


def _date_role_matches(kind: DateKind, fragment: str) -> bool:
    text = re.sub(r"[^a-z0-9&]+", " ", fragment.casefold()).strip()

    def has(pattern: str) -> bool:
        return re.search(pattern, text) is not None

    deadline = r"\b(?:due|deadline|closing|closes?|close date|cutoff)\b"
    if kind == DateKind.RESPONSE_DEADLINE:
        return has(r"\b(?:responses?|proposals?|offers?|submissions?|bids?|quotes?)\b") \
            and has(deadline)
    if kind == DateKind.QA_DEADLINE:
        return has(r"\b(?:q\s*&\s*a|questions?|inquiries?)\b") \
            and has(deadline)
    if kind == DateKind.SITE_VISIT:
        return has(r"\bsite visit\b")
    if kind == DateKind.INDUSTRY_DAY:
        return has(r"\bindustry day\b")
    if kind == DateKind.FORECAST_SOLICITATION:
        return has(r"\bsolicitation\b") and has(
            r"\b(?:forecast|anticipated|expected|planned|target|release)\w*\b"
        )
    if kind == DateKind.FORECAST_AWARD:
        return has(r"\baward\b") and has(
            r"\b(?:forecast|anticipated|expected|planned|target)\w*\b"
        )
    if kind == DateKind.PROGRAM_DEADLINE:
        return has(
            r"\b(?:program|topic|application|white paper|concept paper|"
            r"proposal|submission)\b"
        ) and has(deadline)
    if kind == DateKind.BUDGET_MILESTONE:
        return has(r"\b(?:budget|appropriation|fiscal)\b") and has(
            r"\b(?:milestone|request|deadline|due|enacted|release|submission)\b"
        )
    if kind in (DateKind.ON_RAMP_OPEN, DateKind.ON_RAMP_CLOSE):
        return has(r"\bon ramp\b")
    if kind == DateKind.ORDERING_PERIOD_END:
        return has(r"\b(?:ordering period|last date to order)\b")
    if kind == DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK:
        return has(r"\b(?:vehicle|contract|option)\b") and has(
            r"\b(?:option end|period of performance end|completion date)\b"
        )
    if kind == DateKind.MONITOR_DATE:
        return True
    return False


def _date_role_binds_to_value(value: DateValue, fragment: str) -> bool:
    """Require the typed value to be the date nearest its semantic marker."""

    if not _date_role_matches(value.kind, fragment):
        return False
    if value.kind == DateKind.MONITOR_DATE:
        return _text_names_date_value(value, fragment)

    anchor_patterns = {
        DateKind.RESPONSE_DEADLINE:
            r"\b(?:due|deadline|closing|closes?|close date|cutoff)\b",
        DateKind.QA_DEADLINE:
            r"\b(?:due|deadline|closing|closes?|close date|cutoff)\b",
        DateKind.SITE_VISIT: r"\bsite visit\b",
        DateKind.INDUSTRY_DAY: r"\bindustry day\b",
        DateKind.FORECAST_SOLICITATION: r"\bsolicitation\b",
        DateKind.FORECAST_AWARD: r"\baward\b",
        DateKind.PROGRAM_DEADLINE:
            r"\b(?:due|deadline|closing|closes?|close date|cutoff)\b",
        DateKind.BUDGET_MILESTONE:
            r"\b(?:budget|appropriation|milestone|submission)\b",
        DateKind.ON_RAMP_OPEN: r"\bon ramp\b",
        DateKind.ON_RAMP_CLOSE: r"\bon ramp\b",
        DateKind.ORDERING_PERIOD_END:
            r"\b(?:ordering period|last date to order)\b",
        DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK:
            r"\b(?:option end|period of performance end|completion date)\b",
    }
    anchor_pattern = anchor_patterns.get(value.kind)
    if anchor_pattern is None:
        return False
    anchors = tuple(re.finditer(anchor_pattern, fragment, re.IGNORECASE))
    mentions = _date_mentions(fragment)
    if not anchors or not mentions:
        return False

    target_flags = tuple(
        _date_mention_matches_value(value, match.group(0))
        for match in mentions
    )
    if not any(target_flags):
        return False

    for anchor in anchors:
        distances = tuple(
            _span_distance(anchor.span(), mention.span())
            for mention in mentions
        )
        nearest = min(distances)
        nearest_targets = tuple(
            target_flags[index]
            for index, distance in enumerate(distances)
            if distance == nearest
        )
        if nearest_targets and all(nearest_targets):
            return True
    return False


def _date_mentions(text: str) -> tuple[re.Match[str], ...]:
    month = (
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    )
    pattern = re.compile(
        rf"(?<![a-z0-9])(?:"
        rf"{month}\s+[0-9]{{1,2}}\s*(?:-|–|—|to|through)\s*"
        rf"[0-9]{{1,2}}\s*,?\s*[0-9]{{4}}|"
        rf"{month}\s+[0-9]{{1,2}}\s*,?\s*[0-9]{{4}}|"
        rf"{month}\s+[0-9]{{4}}|"
        r"fy\s*[0-9]{4}(?:\s+q[1-4])?|"
        r"q[1-4]\s+fy\s*[0-9]{4}|"
        r"[0-9]{4}\s+q[1-4]|q[1-4]\s+[0-9]{4}|"
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}|"
        r"[0-9]{1,4}/[0-9]{1,2}/[0-9]{2,4}"
        r")(?![a-z0-9])",
        re.IGNORECASE,
    )
    return tuple(pattern.finditer(text))


def _date_mention_matches_value(value: DateValue, mention: str) -> bool:
    if value.precision == DatePrecision.DAY:
        return value.start is not None \
            and _source_text_names_day(mention, value.start)
    if value.precision == DatePrecision.RANGE:
        return value.start is not None and value.end is not None \
            and (
                _source_text_names_range(mention, value.start, value.end)
                or _source_text_names_day(mention, value.start)
                or _source_text_names_day(mention, value.end)
            )
    if value.precision == DatePrecision.MONTH:
        return _source_text_names_month(mention, value.sort_date)
    if value.precision == DatePrecision.QUARTER:
        return _quarter_source_start(mention) == value.sort_date
    if value.precision == DatePrecision.FISCAL_YEAR:
        return _fiscal_year_source_start(mention) == value.sort_date
    return False


def _span_distance(first: tuple[int, int], second: tuple[int, int]) -> int:
    if first[1] <= second[0]:
        return second[0] - first[1]
    if second[1] <= first[0]:
        return first[0] - second[1]
    return 0


class MoneyBasis(str, Enum):
    OBLIGATED_TO_DATE = "obligated_to_date"
    AWARD_CEILING = "award_ceiling"
    PUBLISHED_EXACT_ESTIMATE = "published_exact_estimate"
    PUBLISHED_RANGE = "published_range"
    BUDGET_REQUEST = "budget_request"
    AGGREGATE_HISTORY = "aggregate_history"
    ANALYST_PIPELINE_VALUE = "analyst_pipeline_value"


class MoneyValue(_FrozenContract):
    money_id: str = Field(min_length=1)
    basis: MoneyBasis
    currency: Literal["USD"] = "USD"
    amount: Optional[Decimal] = Field(default=None, ge=0)
    minimum: Optional[Decimal] = Field(default=None, ge=0)
    maximum: Optional[Decimal] = Field(default=None, ge=0)
    as_of: date
    source_field: Optional[str] = None
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    analyst_decision_id: Optional[str] = None

    @model_validator(mode="after")
    def _money_meaning_is_unambiguous(self) -> "MoneyValue":
        if self.basis == MoneyBasis.PUBLISHED_RANGE:
            if self.amount is not None or self.minimum is None or self.maximum is None:
                raise ValueError("published range requires only minimum and maximum")
            if self.maximum < self.minimum:
                raise ValueError("money range maximum is below minimum")
        else:
            if self.amount is None or self.minimum is not None or self.maximum is not None:
                raise ValueError("exact money basis requires only amount")
        _unique(self.evidence_ids, "money evidence IDs")
        if self.basis == MoneyBasis.ANALYST_PIPELINE_VALUE:
            if not self.analyst_decision_id:
                raise ValueError("pipeline value requires an attributed analyst decision")
        else:
            if not self.source_field or not self.evidence_ids:
                raise ValueError("source money requires source_field and evidence")
            if self.analyst_decision_id:
                raise ValueError("source money cannot masquerade as analyst pipeline value")
        return self

    @property
    def display_value(self) -> str:
        if self.basis == MoneyBasis.PUBLISHED_RANGE:
            return (
                f"{_format_usd(self.minimum)} to {_format_usd(self.maximum)}"
            )
        return _format_usd(self.amount)


def _format_usd(value: Optional[Decimal]) -> str:
    if value is None:
        raise ValueError("money display requires a numeric value")
    for scale, suffix in (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    ):
        if value >= scale:
            shown = (value / scale).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            text = format(shown, "f").rstrip("0").rstrip(".")
            return f"${text}{suffix}"
    shown = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{shown:,.2f}".rstrip("0").rstrip(".")
    return f"${text}"


class CandidateKind(str, Enum):
    CURRENT_NOTICE = "current_notice"
    RESEARCH_CORRIDOR = "research_corridor"


class LifecycleKind(str, Enum):
    EARLY_SIGNAL = "early_signal"
    FUNDED_INTENT = "funded_intent"
    ACQUISITION_PLANNING = "acquisition_planning"
    MARKET_RESEARCH = "market_research"
    PRESOLICITATION = "presolicitation"
    LIVE_SOLICITATION = "live_solicitation"
    AWARD_HISTORY = "award_history"
    RECOMPETE_RESEARCH = "recompete_research"
    CONFIRMED_RECOMPETE = "confirmed_recompete"


class DecisionBoundary(_FrozenContract):
    """Fields that must remain equal when records share one analyst action."""

    buyer_key: str = Field(min_length=1)
    deadline: Optional[date] = None
    eligibility_key: str = Field(min_length=1)
    lifecycle: LifecycleKind
    program_key: str = Field(min_length=1)
    access_route_key: str = Field(min_length=1)
    analyst_action_key: str = Field(min_length=1)


class ProcurementFamilyIdentity(_FrozenContract):
    """A SAM family names both issuing office and solicitation.

    Missing either component fails closed to one exact notice identity, which
    prevents same-number procurements from unrelated offices from merging.
    """

    source_system: Literal["sam.gov"] = "sam.gov"
    issuing_office: Optional[str] = None
    solicitation_number: Optional[str] = None
    fallback_notice_identity: Optional[SourceIdentity] = None

    @model_validator(mode="after")
    def _family_is_namespaced_or_exact(self) -> "ProcurementFamilyIdentity":
        named = bool(self.issuing_office and self.solicitation_number)
        if named == bool(self.fallback_notice_identity):
            raise ValueError(
                "procurement family requires office+solicitation or exact notice fallback"
            )
        if self.fallback_notice_identity \
                and self.fallback_notice_identity.source_system.casefold() != "sam.gov":
            raise ValueError("procurement family fallback must be a SAM.gov notice")
        return self

    @property
    def canonical_key(self) -> str:
        if self.fallback_notice_identity:
            return f"sam.gov:notice:{self.fallback_notice_identity.record_id.casefold()}"
        return (
            f"sam.gov:family:{self.issuing_office.casefold()}:"
            f"{self.solicitation_number.casefold()}"
        )


class StrategicCorridorIdentity(_FrozenContract):
    corridor_id: str = Field(min_length=1)
    decision_boundary: DecisionBoundary


class CandidateMember(_FrozenContract):
    source_identity: SourceIdentity
    member_evidence_ids: tuple[str, ...] = Field(min_length=1)
    decision_boundary: DecisionBoundary
    procurement_family: Optional[ProcurementFamilyIdentity] = None

    @model_validator(mode="after")
    def _member_refs_are_unique(self) -> "CandidateMember":
        _unique(self.member_evidence_ids, "member evidence IDs")
        return self


class AnalystDecision(_FrozenContract):
    """Optional human-owned treatment; no system field can populate it."""

    decision_id: str = Field(min_length=1)
    provenance: Literal["analyst"] = "analyst"
    decided_by: str = Field(min_length=1)
    decided_at: datetime
    treatment: Optional[str] = None
    pipeline_value: Optional[MoneyValue] = None
    win_probability: Optional[Decimal] = Field(default=None, ge=0, le=1)
    note: str = ""

    @model_validator(mode="after")
    def _decision_is_attributed(self) -> "AnalystDecision":
        _require_aware(self.decided_at, "analyst decision timestamp")
        if self.pipeline_value:
            if self.pipeline_value.basis != MoneyBasis.ANALYST_PIPELINE_VALUE:
                raise ValueError("analyst pipeline_value uses the wrong money basis")
            if self.pipeline_value.analyst_decision_id != self.decision_id:
                raise ValueError("pipeline value is not bound to this analyst decision")
        return self


class ClusterLevel(str, Enum):
    EXACT_RECORD = "exact_record"
    PROCUREMENT_FAMILY = "procurement_family"
    STRATEGIC_CORRIDOR = "strategic_corridor"


class CandidateUnit(_FrozenContract):
    candidate_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    kind: CandidateKind
    title: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    component: Optional[str] = None
    office: Optional[str] = None
    lifecycle: LifecycleKind
    cluster_level: ClusterLevel
    members: tuple[CandidateMember, ...] = Field(min_length=1)
    procurement_family: Optional[ProcurementFamilyIdentity] = None
    strategic_corridor: Optional[StrategicCorridorIdentity] = None
    supporting_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    counterevidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    dates: tuple[DateValue, ...] = Field(default_factory=tuple)
    money: tuple[MoneyValue, ...] = Field(default_factory=tuple)
    records_show: str = Field(min_length=1)
    may_suggest: str = Field(min_length=1)
    validate_next: str = Field(min_length=1)
    cluster_reason: str = Field(min_length=1)
    strategic_action: str = Field(min_length=1)
    distinctness_explanation: str = Field(min_length=1)
    inference_chain: str = Field(min_length=1)
    falsifier: str = Field(min_length=1)
    watch_trigger: str = Field(min_length=1)
    cautions: tuple[str, ...] = Field(default_factory=tuple)
    analyst_decision: Optional[AnalystDecision] = None

    @model_validator(mode="after")
    def _candidate_identity_and_action_are_exact(self) -> "CandidateUnit":
        record_keys = tuple(member.source_identity.canonical_key for member in self.members)
        _unique(record_keys, "candidate member source identities")
        member_evidence = tuple(
            evidence_id
            for member in self.members
            for evidence_id in member.member_evidence_ids
        )
        _unique(member_evidence, "candidate member evidence ownership")
        _unique(self.supporting_evidence_ids, "candidate supporting evidence IDs")
        _unique(self.counterevidence_ids, "candidate counterevidence IDs")
        _unique(tuple(item.date_id for item in self.dates), "candidate date IDs")
        _unique(tuple(item.money_id for item in self.money), "candidate money IDs")
        if any(member.decision_boundary.lifecycle != self.lifecycle
               for member in self.members):
            raise ValueError("candidate lifecycle differs from a member boundary")
        if len(self.members) > 1:
            boundary = self.members[0].decision_boundary
            if any(member.decision_boundary != boundary for member in self.members[1:]):
                raise ValueError(
                    "buyer, deadline, eligibility, lifecycle, program, access route, "
                    "and analyst action must match before records can cluster"
                )
        if self.cluster_level == ClusterLevel.EXACT_RECORD:
            if len(self.members) != 1 or self.procurement_family \
                    or self.strategic_corridor:
                raise ValueError("exact-record candidates contain one unclustered member")
        elif self.cluster_level == ClusterLevel.PROCUREMENT_FAMILY:
            if not self.procurement_family or self.strategic_corridor:
                raise ValueError("procurement-family cluster requires only family identity")
        elif self.cluster_level == ClusterLevel.STRATEGIC_CORRIDOR:
            if not self.strategic_corridor:
                raise ValueError("strategic corridor requires corridor identity")
            if any(member.decision_boundary != self.strategic_corridor.decision_boundary
                   for member in self.members):
                raise ValueError("corridor members do not share its decision boundary")
        if self.procurement_family:
            if any(member.procurement_family != self.procurement_family
                   for member in self.members):
                raise ValueError(
                    "candidate procurement family is not bound to every member"
                )
            fallback = self.procurement_family.fallback_notice_identity
            if fallback and not any(
                member.source_identity.canonical_key == fallback.canonical_key
                for member in self.members
            ):
                raise ValueError(
                    "procurement family fallback does not match a member notice"
                )
        elif any(member.procurement_family is not None for member in self.members):
            raise ValueError("member procurement family is absent from candidate identity")
        if self.kind == CandidateKind.CURRENT_NOTICE \
                and self.cluster_level == ClusterLevel.STRATEGIC_CORRIDOR:
            raise ValueError("current notices cannot masquerade as strategic corridors")
        if self.kind == CandidateKind.RESEARCH_CORRIDOR \
                and self.cluster_level == ClusterLevel.EXACT_RECORD:
            raise ValueError("research corridors require family or corridor identity")
        notice_lifecycles = {
            LifecycleKind.MARKET_RESEARCH,
            LifecycleKind.PRESOLICITATION,
            LifecycleKind.LIVE_SOLICITATION,
        }
        if self.kind == CandidateKind.CURRENT_NOTICE \
                and self.lifecycle not in notice_lifecycles:
            raise ValueError("current notice uses a non-notice lifecycle")
        if self.kind == CandidateKind.RESEARCH_CORRIDOR \
                and self.lifecycle in notice_lifecycles:
            raise ValueError(
                "research corridor cannot claim an official notice lifecycle"
            )
        if any(item.kind == DateKind.CONFIRMED_RECOMPETE
               for item in self.dates) \
                and self.lifecycle != LifecycleKind.CONFIRMED_RECOMPETE:
            raise ValueError(
                "confirmed-recompete date requires confirmed-recompete lifecycle"
            )
        if any(item.kind in VEHICLE_MILESTONE_DATE_KINDS
               for item in self.dates):
            raise ValueError(
                "vehicle milestones belong to a vehicle signal, not a candidate"
            )
        if self.analyst_decision:
            decision_money_ids = {
                self.analyst_decision.pipeline_value.money_id
            } if self.analyst_decision.pipeline_value else set()
            if decision_money_ids & {item.money_id for item in self.money}:
                raise ValueError("analyst pipeline value cannot duplicate source money")
        if any(item.basis == MoneyBasis.ANALYST_PIPELINE_VALUE
               for item in self.money):
            raise ValueError(
                "pipeline value belongs only inside an attributed analyst decision"
            )
        assert_candidate_review_language(
            self.title,
            self.records_show,
            self.may_suggest,
            self.validate_next,
            self.cluster_reason,
            self.strategic_action,
            self.distinctness_explanation,
            self.inference_chain,
            self.falsifier,
            self.watch_trigger,
            *self.cautions,
        )
        return self

    @property
    def member_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            evidence_id
            for member in self.members
            for evidence_id in member.member_evidence_ids
        )

    @property
    def all_evidence_ids(self) -> tuple[str, ...]:
        ordered = (
            *self.member_evidence_ids,
            *self.supporting_evidence_ids,
            *self.counterevidence_ids,
            *(eid for item in self.dates for eid in item.evidence_ids),
            *(eid for item in self.money for eid in item.evidence_ids),
        )
        return tuple(dict.fromkeys(ordered))


class VehicleAccessPosture(str, Enum):
    DIRECT_HOLDER = "direct_holder"
    TEAMING_REQUIRED = "teaming_required"
    CHANNEL_OR_RESELLER = "channel_or_reseller"
    ACCESS_NOT_ESTABLISHED = "access_not_established"
    VALIDATION_REQUIRED = "validation_required"
    UNKNOWN = "unknown"


class VehicleRelationshipKind(str, Enum):
    VEHICLE_ONLY = "vehicle_only"
    TASK_ORDER = "task_order"
    DELIVERY_ORDER = "delivery_order"
    BPA_CALL = "bpa_call"
    OTHER_ORDER = "other_order"


class VehicleSignalKind(str, Enum):
    ACCESS_PATH = "access_path"
    TASK_ORDER_ACTIVITY = "task_order_activity"
    VEHICLE_ESTABLISHMENT = "vehicle_establishment"
    ON_RAMP = "on_ramp"
    ORDERING_PERIOD = "ordering_period"
    CONFIRMED_RECOMPETE = "confirmed_recompete"


class VehicleSignalStatus(str, Enum):
    CURRENT = "current"
    UPCOMING = "upcoming"
    CLOSED = "closed"
    RESEARCH = "research"


class VehicleClass(str, Enum):
    GWAC = "gwac"
    MULTIPLE_AWARD_IDIQ = "multiple_award_idiq"
    SINGLE_AWARD_IDIQ = "single_award_idiq"
    BPA = "bpa"
    FEDERAL_SUPPLY_SCHEDULE = "federal_supply_schedule"
    ENTERPRISE_VEHICLE = "enterprise_vehicle"
    OTHER = "other"


class VehicleOrderingStatus(str, Enum):
    ACTIVE = "active"
    UPCOMING = "upcoming"
    CLOSED = "closed"
    NOT_ESTABLISHED = "not_established"


class VehicleOnRampStatus(str, Enum):
    OPEN = "open"
    UPCOMING = "upcoming"
    CLOSED = "closed"
    NONE_ANNOUNCED = "none_announced"
    NOT_ESTABLISHED = "not_established"


class VehicleParticipantRole(str, Enum):
    CLIENT_HOLDER = "client_holder"
    HOLDER = "holder"
    AWARDEE = "awardee"


class VehicleParticipant(_FrozenContract):
    participant_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    role: VehicleParticipantRole
    uei: Optional[str] = None
    cage_code: Optional[str] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _participant_is_evidence_bound(self) -> "VehicleParticipant":
        _unique(self.evidence_ids, "vehicle participant evidence IDs")
        assert_candidate_review_language(self.legal_name)
        return self


class VehicleActivityIdentity(_FrozenContract):
    activity_id: str = Field(min_length=1)
    source_identity: SourceIdentity
    related_vehicle_identity: Optional[SourceIdentity] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _activity_is_evidence_bound(self) -> "VehicleActivityIdentity":
        _unique(self.evidence_ids, "vehicle activity evidence IDs")
        if self.related_vehicle_identity is not None and (
            self.related_vehicle_identity.canonical_key
            == self.source_identity.canonical_key
        ):
            raise ValueError(
                "vehicle and activity source identities must remain separate"
            )
        return self


class VehiclePartnerRoute(_FrozenContract):
    route_id: str = Field(min_length=1)
    partner_name: str = Field(min_length=1)
    route_description: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _route_is_non_dispositive(self) -> "VehiclePartnerRoute":
        _unique(self.evidence_ids, "vehicle partner-route evidence IDs")
        assert_candidate_review_language(
            self.partner_name,
            self.route_description,
        )
        return self


class VehicleWatchRecord(_FrozenContract):
    """Section 3 vehicle intelligence with exact lineage and no disposition."""

    watch_record_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_sha256: str
    canonical_name: Optional[str] = None
    aliases: tuple[str, ...] = Field(default_factory=tuple)
    vehicle_program_id: Optional[str] = None
    parent_idv_piid: Optional[str] = None
    vehicle_source_identity: Optional[SourceIdentity] = None
    parent_idv_source_identity: Optional[SourceIdentity] = None
    vehicle_class: Optional[VehicleClass] = None
    identity_evidence_ids: tuple[str, ...] = Field(min_length=1)
    managing_agency: str = Field(min_length=1)
    managing_component: Optional[str] = None
    program_office: Optional[str] = None
    management_evidence_ids: tuple[str, ...] = Field(min_length=1)
    scope_summary: Optional[str] = None
    pools: tuple[str, ...] = Field(default_factory=tuple)
    domains: tuple[str, ...] = Field(default_factory=tuple)
    sins: tuple[str, ...] = Field(default_factory=tuple)
    naics_codes: tuple[str, ...] = Field(default_factory=tuple)
    psc_codes: tuple[str, ...] = Field(default_factory=tuple)
    eligible_ordering_organizations: tuple[str, ...] = Field(
        default_factory=tuple,
    )
    scope_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    ordering_status: VehicleOrderingStatus
    on_ramp_status: VehicleOnRampStatus
    status_evidence_ids: tuple[str, ...] = Field(min_length=1)
    dates: tuple[DateValue, ...] = Field(default_factory=tuple)
    participants: tuple[VehicleParticipant, ...] = Field(default_factory=tuple)
    access_posture: VehicleAccessPosture
    access_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    partner_route: Optional[VehiclePartnerRoute] = None
    notice_activity: tuple[VehicleActivityIdentity, ...] = Field(
        default_factory=tuple,
    )
    order_activity: tuple[VehicleActivityIdentity, ...] = Field(
        default_factory=tuple,
    )
    award_activity: tuple[VehicleActivityIdentity, ...] = Field(
        default_factory=tuple,
    )
    ceiling_or_value: Optional[MoneyValue] = None
    records_show: str = Field(min_length=1)
    may_suggest: str = Field(min_length=1)
    validate_next: str = Field(min_length=1)
    official_evidence_ids: tuple[str, ...] = Field(min_length=1)
    last_checked_at: datetime
    source_data_as_of: Optional[date] = None
    coverage_query_ids: tuple[str, ...] = Field(min_length=1)
    creates_candidate: Literal[False] = False

    @model_validator(mode="after")
    def _watch_record_is_explicit_and_non_dispositive(
        self,
    ) -> "VehicleWatchRecord":
        _validate_hash(self.scope_sha256, "vehicle watch scope_sha256")
        _require_aware(self.last_checked_at, "vehicle watch last_checked_at")
        for label, values in (
            ("vehicle aliases", tuple(value.casefold() for value in self.aliases)),
            ("vehicle pools", tuple(value.casefold() for value in self.pools)),
            ("vehicle domains", tuple(value.casefold() for value in self.domains)),
            ("vehicle SINs", tuple(value.casefold() for value in self.sins)),
            ("vehicle NAICS", tuple(value.casefold() for value in self.naics_codes)),
            ("vehicle PSCs", tuple(value.casefold() for value in self.psc_codes)),
            (
                "eligible ordering organizations",
                tuple(value.casefold() for value in self.eligible_ordering_organizations),
            ),
            ("vehicle identity evidence IDs", self.identity_evidence_ids),
            ("vehicle management evidence IDs", self.management_evidence_ids),
            ("vehicle scope evidence IDs", self.scope_evidence_ids),
            ("vehicle status evidence IDs", self.status_evidence_ids),
            ("vehicle access evidence IDs", self.access_evidence_ids),
            ("vehicle official evidence IDs", self.official_evidence_ids),
            ("vehicle coverage query IDs", self.coverage_query_ids),
        ):
            _unique(values, label)
        _unique(
            tuple(item.date_id for item in self.dates),
            "vehicle watch date IDs",
        )
        _unique(
            tuple(item.participant_id for item in self.participants),
            "vehicle participant IDs",
        )
        activities = (
            *self.notice_activity,
            *self.order_activity,
            *self.award_activity,
        )
        _unique(
            tuple(item.activity_id for item in activities),
            "vehicle activity IDs",
        )
        _unique(
            tuple(item.source_identity.canonical_key for item in activities),
            "vehicle activity source identities",
        )
        if not any((
            self.canonical_name,
            self.vehicle_program_id,
            self.parent_idv_piid,
        )):
            raise ValueError(
                "vehicle identity requires a sourced name, program ID, or "
                "parent IDV PIID"
            )
        if not any((
            self.vehicle_source_identity,
            self.parent_idv_source_identity,
        )):
            raise ValueError(
                "vehicle watch requires an exact vehicle or parent IDV "
                "source identity"
            )
        if self.parent_idv_piid and self.parent_idv_source_identity is None:
            raise ValueError(
                "parent IDV PIID requires an exact parent IDV source identity"
            )
        if self.vehicle_source_identity is not None \
                and self.parent_idv_source_identity is not None \
                and self.vehicle_source_identity.canonical_key \
                == self.parent_idv_source_identity.canonical_key:
            raise ValueError(
                "vehicle and parent IDV source identities must remain separate"
            )
        scope_values_present = any((
            self.scope_summary,
            self.pools,
            self.domains,
            self.sins,
            self.naics_codes,
            self.psc_codes,
            self.eligible_ordering_organizations,
        ))
        if scope_values_present != bool(self.scope_evidence_ids):
            raise ValueError(
                "vehicle scope values and evidence must be supplied together"
            )
        if any(
            timing.kind not in VEHICLE_MILESTONE_DATE_KINDS
            for timing in self.dates
        ):
            raise ValueError("vehicle watch carries a non-vehicle milestone")
        if self.on_ramp_status in {
            VehicleOnRampStatus.OPEN,
            VehicleOnRampStatus.UPCOMING,
        } and not any(
            timing.kind in {DateKind.ON_RAMP_OPEN, DateKind.ON_RAMP_CLOSE}
            for timing in self.dates
        ):
            raise ValueError("open or upcoming on-ramp requires an exact date")
        if self.access_posture is VehicleAccessPosture.DIRECT_HOLDER and not any(
            row.role is VehicleParticipantRole.CLIENT_HOLDER
            for row in self.participants
        ):
            raise ValueError("direct-holder posture requires a client-holder row")
        positive_access_postures = {
            VehicleAccessPosture.TEAMING_REQUIRED,
            VehicleAccessPosture.CHANNEL_OR_RESELLER,
        }
        if self.access_posture in positive_access_postures \
                and self.partner_route is None:
            raise ValueError("evidenced partner access requires a partner route")
        if self.access_posture in {
            VehicleAccessPosture.DIRECT_HOLDER,
            *positive_access_postures,
        } and not self.access_evidence_ids:
            raise ValueError(
                "positive vehicle access posture requires exact evidence"
            )
        if (self.order_activity or self.award_activity) \
                and self.source_data_as_of is None:
            raise ValueError(
                "order or award activity requires source_data_as_of"
            )
        if any(
            activity.related_vehicle_identity is None
            for activity in (*self.order_activity, *self.award_activity)
        ):
            raise ValueError(
                "order or award activity requires an exact related vehicle "
                "identity"
            )
        expected_activity_parent = (
            self.parent_idv_source_identity or self.vehicle_source_identity
        )
        if any(
            activity.related_vehicle_identity is not None
            and expected_activity_parent is not None
            and activity.related_vehicle_identity.canonical_key
            != expected_activity_parent.canonical_key
            for activity in (*self.order_activity, *self.award_activity)
        ):
            raise ValueError(
                "order or award activity must reference this record's exact "
                "parent IDV or vehicle source identity"
            )
        if self.ceiling_or_value is not None and (
            self.ceiling_or_value.basis is MoneyBasis.ANALYST_PIPELINE_VALUE
        ):
            raise ValueError("vehicle watch cannot carry analyst pipeline value")
        nested_evidence = {
            *self.identity_evidence_ids,
            *self.management_evidence_ids,
            *self.scope_evidence_ids,
            *self.status_evidence_ids,
            *self.access_evidence_ids,
            *(evidence_id for item in self.dates for evidence_id in item.evidence_ids),
            *(evidence_id for item in self.participants for evidence_id in item.evidence_ids),
            *(evidence_id for item in activities for evidence_id in item.evidence_ids),
            *(self.partner_route.evidence_ids if self.partner_route else ()),
            *(self.ceiling_or_value.evidence_ids if self.ceiling_or_value else ()),
        }
        if not nested_evidence.issubset(set(self.official_evidence_ids)):
            raise ValueError(
                "vehicle watch nested evidence must be retained as official evidence"
            )
        assert_candidate_review_language(
            self.canonical_name or "",
            *self.aliases,
            self.managing_agency,
            self.managing_component or "",
            self.program_office or "",
            self.scope_summary or "",
            *self.pools,
            *self.domains,
            *self.eligible_ordering_organizations,
            self.records_show,
            self.may_suggest,
            self.validate_next,
        )
        return self

    @property
    def all_evidence_ids(self) -> tuple[str, ...]:
        return self.official_evidence_ids


class VehicleIdentity(_FrozenContract):
    """Evidence-owned vehicle identity; a PIID never implies a vehicle name."""

    vehicle_id: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    name: Optional[str] = None
    idv_piid: Optional[str] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _identity_is_explicit(self) -> "VehicleIdentity":
        _unique(self.evidence_ids, "vehicle identity evidence IDs")
        if not self.name and not self.idv_piid:
            raise ValueError("vehicle identity requires a sourced name or IDV PIID")
        assert_candidate_review_language(
            self.agency,
            self.name or "",
            self.idv_piid or "",
        )
        return self


class VehicleRelationship(_FrozenContract):
    relationship_id: str = Field(min_length=1)
    vehicle_id: str = Field(min_length=1)
    kind: VehicleRelationshipKind
    access_posture: VehicleAccessPosture
    order_identity: Optional[SourceIdentity] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _relationship_is_exact(self) -> "VehicleRelationship":
        _unique(self.evidence_ids, "vehicle relationship evidence IDs")
        order_kinds = {
            VehicleRelationshipKind.TASK_ORDER,
            VehicleRelationshipKind.DELIVERY_ORDER,
            VehicleRelationshipKind.BPA_CALL,
            VehicleRelationshipKind.OTHER_ORDER,
        }
        if (self.kind in order_kinds) != bool(self.order_identity):
            raise ValueError(
                "vehicle order relationship and exact order identity must "
                "be supplied together"
            )
        return self


class VehicleSignal(_FrozenContract):
    """A monitored acquisition-access fact, never a candidate disposition."""

    signal_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_sha256: str
    title: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    kind: VehicleSignalKind
    status: VehicleSignalStatus
    vehicle: VehicleIdentity
    relationship: Optional[VehicleRelationship] = None
    dates: tuple[DateValue, ...] = Field(default_factory=tuple)
    candidate_ids: tuple[str, ...] = Field(default_factory=tuple)
    records_show: str = Field(min_length=1)
    may_suggest: str = Field(min_length=1)
    validate_next: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    last_checked_at: datetime
    creates_candidate: Literal[False] = False

    @model_validator(mode="after")
    def _signal_is_bound_and_non_dispositive(self) -> "VehicleSignal":
        _validate_hash(self.scope_sha256, "vehicle signal scope_sha256")
        _require_aware(self.last_checked_at, "vehicle signal last_checked_at")
        _unique(tuple(item.date_id for item in self.dates),
                "vehicle signal date IDs")
        _unique(self.candidate_ids, "vehicle signal candidate IDs")
        _unique(self.evidence_ids, "vehicle signal evidence IDs")
        if self.relationship is not None:
            if self.relationship.vehicle_id != self.vehicle.vehicle_id:
                raise ValueError(
                    "vehicle relationship references another vehicle identity"
                )
            order_kinds = {
                VehicleRelationshipKind.TASK_ORDER,
                VehicleRelationshipKind.DELIVERY_ORDER,
                VehicleRelationshipKind.BPA_CALL,
                VehicleRelationshipKind.OTHER_ORDER,
            }
            if self.relationship.kind in order_kinds and not self.vehicle.idv_piid:
                raise ValueError(
                    "vehicle order relationship requires an exact parent IDV PIID"
                )
        if self.kind == VehicleSignalKind.TASK_ORDER_ACTIVITY \
                and (self.relationship is None or self.relationship.kind
                     == VehicleRelationshipKind.VEHICLE_ONLY):
            raise ValueError(
                "task-order activity requires an exact vehicle order relationship"
            )
        allowed_date_kinds = {
            *VEHICLE_MILESTONE_DATE_KINDS,
            DateKind.CONFIRMED_RECOMPETE,
        }
        if any(item.kind not in allowed_date_kinds for item in self.dates):
            raise ValueError("vehicle signal carries a non-vehicle milestone")
        if self.kind == VehicleSignalKind.CONFIRMED_RECOMPETE \
                and not any(item.kind == DateKind.CONFIRMED_RECOMPETE
                            for item in self.dates):
            raise ValueError(
                "confirmed vehicle recompete requires an explicit confirmed date"
            )
        assert_candidate_review_language(
            self.title,
            self.agency,
            self.records_show,
            self.may_suggest,
            self.validate_next,
        )
        return self

    @property
    def all_evidence_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((
            *self.evidence_ids,
            *self.vehicle.evidence_ids,
            *(self.relationship.evidence_ids
              if self.relationship is not None else ()),
            *(evidence_id for timing in self.dates
              for evidence_id in timing.evidence_ids),
        )))


def validate_vehicle_signal_evidence(
    signal: VehicleSignal,
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    evidence_by_id: dict[str, EvidenceRecord],
    candidate_ids: tuple[str, ...] = (),
) -> None:
    """Fail closed unless a vehicle signal resolves to exact official facts."""

    if (signal.client_id, signal.run_id, signal.scope_sha256) != (
        binding.client_id, binding.run_id, binding.scope_sha256,
    ):
        raise ValueError("vehicle signal belongs to another client, run, or scope")
    if signal.last_checked_at > as_of:
        raise ValueError("vehicle signal verification postdates document as-of")
    if any(candidate_id not in candidate_ids
           for candidate_id in signal.candidate_ids):
        raise ValueError("vehicle signal references an unknown candidate")
    missing = tuple(
        evidence_id for evidence_id in signal.all_evidence_ids
        if evidence_id not in evidence_by_id
    )
    if missing:
        raise ValueError(
            f"vehicle signal {signal.signal_id} has unresolved evidence "
            f"references: {list(missing)}"
        )
    accepted_refs = tuple(
        evidence_by_id[evidence_id]
        for evidence_id in signal.all_evidence_ids
    )
    if accepted_refs and signal.last_checked_at < max(
        record.retrieved_at for record in accepted_refs
    ):
        raise ValueError(
            "vehicle signal last-checked time predates accepted evidence"
        )
    _validate_watch_freshness(
        label="vehicle signal",
        last_checked_at=signal.last_checked_at,
        as_of=as_of,
        current=signal.status in {
            VehicleSignalStatus.CURRENT,
            VehicleSignalStatus.UPCOMING,
        },
        milestone_dates=tuple(
            timing.sort_date for timing in signal.dates
            if timing.kind in {
                DateKind.ON_RAMP_OPEN,
                DateKind.ON_RAMP_CLOSE,
            }
        ),
    )

    allowed_identity_sources = {
        EvidenceKind.VEHICLE,
        EvidenceKind.NOTICE,
        EvidenceKind.AWARD,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }
    identity_refs = tuple(
        evidence_by_id[evidence_id]
        for evidence_id in signal.vehicle.evidence_ids
    )
    if not identity_refs or not all(
        ref.official_source and ref.primary_source
        and ref.source_kind in allowed_identity_sources
        for ref in identity_refs
    ):
        raise ValueError(
            "vehicle identity requires exact official primary evidence"
        )
    if signal.vehicle.name and not any(
        any(
            span.assertion == EvidenceAssertion.VEHICLE_IDENTITY
            and signal.vehicle.name.casefold() in span.quote.casefold()
            for span in ref.assertion_spans
        )
        for ref in identity_refs
    ):
        raise ValueError(
            "vehicle name requires an exact official identity assertion"
        )
    if signal.vehicle.idv_piid and not any(
        ref.source_identity.record_id.casefold()
        == signal.vehicle.idv_piid.casefold()
        or any(
            span.assertion in {
                EvidenceAssertion.VEHICLE_IDENTITY,
                EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
            }
            and signal.vehicle.idv_piid.casefold() in span.quote.casefold()
            for span in ref.assertion_spans
        )
        for ref in identity_refs
    ):
        raise ValueError(
            "vehicle PIID requires exact source identity or assertion"
        )

    if signal.relationship is not None:
        relationship_refs = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in signal.relationship.evidence_ids
        )
        if not relationship_refs or not all(
            ref.official_source and ref.primary_source
            and ref.source_kind in allowed_identity_sources
            for ref in relationship_refs
        ):
            raise ValueError(
                "vehicle relationship requires official primary evidence"
            )
        order_identity = signal.relationship.order_identity
        if order_identity is not None and not any(
            ref.source_identity.canonical_key == order_identity.canonical_key
            and any(
                span.assertion is EvidenceAssertion.PARENT_IDV_RELATIONSHIP
                and _source_text_names_identifier(
                    span.quote,
                    order_identity.record_id,
                )
                and signal.vehicle.idv_piid is not None
                and _source_text_names_identifier(
                    span.quote,
                    signal.vehicle.idv_piid,
                )
                for span in ref.assertion_spans
            )
            for ref in relationship_refs
        ):
            raise ValueError(
                "vehicle order and parent relationship lacks an exact "
                "official parent-IDV assertion"
            )
        access_assertions = {
            VehicleAccessPosture.DIRECT_HOLDER:
                EvidenceAssertion.VEHICLE_DIRECT_HOLDER,
            VehicleAccessPosture.TEAMING_REQUIRED:
                EvidenceAssertion.VEHICLE_TEAMING_REQUIRED,
            VehicleAccessPosture.CHANNEL_OR_RESELLER:
                EvidenceAssertion.VEHICLE_CHANNEL_OR_RESELLER,
        }
        required_access_assertion = access_assertions.get(
            signal.relationship.access_posture
        )
        if required_access_assertion is not None:
            vehicle_anchors = tuple(
                value.casefold()
                for value in (
                    signal.vehicle.name,
                    signal.vehicle.idv_piid,
                )
                if value
            )
            if not any(
                span.assertion == required_access_assertion
                and binding.client_name.casefold() in span.quote.casefold()
                and any(anchor in span.quote.casefold()
                        for anchor in vehicle_anchors)
                for ref in relationship_refs
                for span in ref.assertion_spans
            ):
                raise ValueError(
                    "non-unknown vehicle access posture requires an exact "
                    "client-and-vehicle evidence assertion"
                )

    signal_refs = tuple(
        evidence_by_id[evidence_id] for evidence_id in signal.evidence_ids
    )
    if not signal_refs or not all(
        ref.official_source and ref.primary_source
        and ref.source_kind in {
            EvidenceKind.VEHICLE,
            EvidenceKind.NOTICE,
            EvidenceKind.AWARD,
            EvidenceKind.AGENCY_FORECAST,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        }
        for ref in signal_refs
    ):
        raise ValueError("vehicle signal requires official primary evidence")
    signal_assertions = {
        VehicleSignalKind.ON_RAMP: EvidenceAssertion.VEHICLE_ON_RAMP,
        VehicleSignalKind.ORDERING_PERIOD:
            EvidenceAssertion.VEHICLE_ORDERING_PERIOD,
        VehicleSignalKind.CONFIRMED_RECOMPETE:
            EvidenceAssertion.EXPLICIT_RECOMPETE,
    }
    required_signal_assertion = signal_assertions.get(signal.kind)
    has_role_notice = any(
        ref.source_kind == EvidenceKind.NOTICE
        and (
            (
                signal.kind == VehicleSignalKind.ON_RAMP
                and ref.notice_role == NoticeRole.VEHICLE_ON_RAMP
            )
            or (
                signal.kind == VehicleSignalKind.VEHICLE_ESTABLISHMENT
                and ref.notice_role == NoticeRole.VEHICLE_ESTABLISHMENT
            )
        )
        for ref in signal_refs
    )
    if signal.kind == VehicleSignalKind.VEHICLE_ESTABLISHMENT \
            and not has_role_notice:
        raise ValueError(
            "vehicle-establishment signal requires that exact notice role"
        )
    if required_signal_assertion is not None \
            and not has_role_notice \
            and not any(ref.has_assertion(required_signal_assertion)
                        for ref in signal_refs):
        raise ValueError("vehicle signal lacks its exact official role assertion")

    vehicle_date_sources = {
        DateKind.ON_RAMP_OPEN: {
            EvidenceKind.VEHICLE,
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
        DateKind.ON_RAMP_CLOSE: {
            EvidenceKind.VEHICLE,
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
        DateKind.ORDERING_PERIOD_END: {
            EvidenceKind.VEHICLE,
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
        DateKind.VEHICLE_OPTION_END_RESEARCH_CLOCK: {
            EvidenceKind.VEHICLE,
            EvidenceKind.AWARD,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
        DateKind.CONFIRMED_RECOMPETE: {
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_FORECAST,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
    }
    for timing in signal.dates:
        timing_refs = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in timing.evidence_ids
        )
        if not any(
            ref.source_kind in vehicle_date_sources[timing.kind]
            and ref.official_source and ref.primary_source
            for ref in timing_refs
        ):
            raise ValueError("vehicle timing lacks compatible official evidence")
        if not evidence_supports_date_value(timing, timing_refs):
            raise ValueError(
                "vehicle timing value is not visible in exact evidence"
            )


def validate_vehicle_watch_record_evidence(
    record: VehicleWatchRecord,
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    evidence_by_id: dict[str, EvidenceRecord],
    coverage_by_query_id: dict[str, "CoverageRecord"],
) -> None:
    """Fail closed on Section 3 vehicle facts, lineage, and freshness."""

    if (record.client_id, record.run_id, record.scope_sha256) != (
        binding.client_id,
        binding.run_id,
        binding.scope_sha256,
    ):
        raise ValueError("vehicle watch belongs to another client, run, or scope")
    if record.last_checked_at > as_of:
        raise ValueError("vehicle watch verification postdates document as-of")
    if record.source_data_as_of is not None and (
        record.source_data_as_of > as_of.date()
        or record.source_data_as_of > record.last_checked_at.date()
    ):
        raise ValueError("vehicle source-data as-of postdates its verification")
    missing = tuple(
        evidence_id for evidence_id in record.all_evidence_ids
        if evidence_id not in evidence_by_id
    )
    if missing:
        raise ValueError(
            "vehicle watch has unresolved evidence references: "
            f"{list(missing)}"
        )
    refs = tuple(
        evidence_by_id[evidence_id]
        for evidence_id in record.all_evidence_ids
    )
    allowed_source_kinds = {
        EvidenceKind.VEHICLE,
        EvidenceKind.NOTICE,
        EvidenceKind.AWARD,
        EvidenceKind.AGENCY_FORECAST,
        EvidenceKind.AGENCY_ANNOUNCEMENT,
    }
    if not refs or not all(
        ref.official_source
        and ref.primary_source
        and ref.source_kind in allowed_source_kinds
        for ref in refs
    ):
        raise ValueError(
            "vehicle watch requires official primary procurement evidence"
        )
    if record.last_checked_at < max(ref.retrieved_at for ref in refs):
        raise ValueError(
            "vehicle watch last-checked time predates accepted evidence"
        )
    if record.source_data_as_of is not None:
        if not any(
            span.assertion is EvidenceAssertion.VEHICLE_SOURCE_DATA_AS_OF
            and _source_text_names_day(span.quote, record.source_data_as_of)
            for ref in refs
            for span in ref.assertion_spans
        ):
            raise ValueError(
                "vehicle source-data as-of lacks an exact official assertion"
            )
    _validate_watch_freshness(
        label="vehicle watch record",
        last_checked_at=record.last_checked_at,
        as_of=as_of,
        current=(
            record.ordering_status in {
                VehicleOrderingStatus.ACTIVE,
                VehicleOrderingStatus.UPCOMING,
            }
            or record.on_ramp_status in {
                VehicleOnRampStatus.OPEN,
                VehicleOnRampStatus.UPCOMING,
            }
        ),
        milestone_dates=tuple(
            timing.sort_date for timing in record.dates
            if timing.kind in {DateKind.ON_RAMP_OPEN, DateKind.ON_RAMP_CLOSE}
        ),
    )

    def asserted(
        evidence_ids: tuple[str, ...],
        assertion: EvidenceAssertion,
        value: Optional[str] = None,
    ) -> bool:
        return any(
            span.assertion is assertion
            and (value is None or value.casefold() in span.quote.casefold())
            for evidence_id in evidence_ids
            for span in evidence_by_id[evidence_id].assertion_spans
        )

    def source_identity_is_bound(
        source_identity: SourceIdentity,
        anchors: tuple[str, ...],
    ) -> bool:
        return any(
            ref.source_identity.canonical_key == source_identity.canonical_key
            and any(
                span.assertion in {
                    EvidenceAssertion.VEHICLE_IDENTITY,
                    EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                }
                and any(
                    _source_text_names_identifier(span.quote, anchor)
                    for anchor in anchors
                )
                for span in ref.assertion_spans
            )
            for evidence_id in record.identity_evidence_ids
            for ref in (evidence_by_id[evidence_id],)
        )

    vehicle_anchors = tuple(value for value in (
        record.canonical_name,
        *record.aliases,
        record.vehicle_program_id,
        record.parent_idv_piid,
    ) if value)
    if record.vehicle_source_identity is not None and not (
        source_identity_is_bound(
            record.vehicle_source_identity,
            vehicle_anchors,
        )
    ):
        raise ValueError(
            "vehicle source identity is not bound to the published vehicle "
            "identity"
        )
    if record.parent_idv_source_identity is not None:
        parent_anchors = tuple(value for value in (
            record.parent_idv_piid,
            record.parent_idv_source_identity.record_id,
        ) if value)
        if not source_identity_is_bound(
            record.parent_idv_source_identity,
            parent_anchors,
        ):
            raise ValueError(
                "parent IDV source identity is not bound to the published "
                "parent identity"
            )

    for value in (
        *((record.canonical_name,) if record.canonical_name else ()),
        *record.aliases,
        *((record.vehicle_program_id,) if record.vehicle_program_id else ()),
        *((record.parent_idv_piid,) if record.parent_idv_piid else ()),
    ):
        if not asserted(
            record.identity_evidence_ids,
            EvidenceAssertion.VEHICLE_IDENTITY,
            value,
        ):
            raise ValueError("vehicle identity value lacks an exact assertion")
    if record.vehicle_class is not None and not asserted(
        record.identity_evidence_ids,
        EvidenceAssertion.VEHICLE_CLASS,
    ):
        raise ValueError("vehicle class lacks an exact official assertion")
    for value in (
        record.managing_agency,
        *((record.managing_component,) if record.managing_component else ()),
        *((record.program_office,) if record.program_office else ()),
    ):
        if not asserted(
            record.management_evidence_ids,
            EvidenceAssertion.VEHICLE_MANAGEMENT,
            value,
        ):
            raise ValueError("vehicle management value lacks an exact assertion")
    scoped_values = (
        *record.pools,
        *record.domains,
        *record.sins,
        *record.naics_codes,
        *record.psc_codes,
    )
    if record.scope_summary is not None and not asserted(
        record.scope_evidence_ids,
        EvidenceAssertion.VEHICLE_SCOPE,
    ):
        raise ValueError("vehicle scope summary lacks an exact assertion")
    for value in scoped_values:
        if not asserted(
            record.scope_evidence_ids,
            EvidenceAssertion.VEHICLE_SCOPE,
            value,
        ):
            raise ValueError("vehicle scope value lacks an exact assertion")
    for value in record.eligible_ordering_organizations:
        if not asserted(
            record.scope_evidence_ids,
            EvidenceAssertion.VEHICLE_ELIGIBILITY,
            value,
        ):
            raise ValueError("vehicle ordering eligibility lacks exact evidence")
    if not asserted(
        record.status_evidence_ids,
        EvidenceAssertion.VEHICLE_ORDERING_STATUS,
    ):
        raise ValueError("vehicle ordering status lacks an exact assertion")
    if record.on_ramp_status in {
        VehicleOnRampStatus.OPEN,
        VehicleOnRampStatus.UPCOMING,
        VehicleOnRampStatus.CLOSED,
    } and not asserted(
        record.status_evidence_ids,
        EvidenceAssertion.VEHICLE_ON_RAMP,
    ):
        raise ValueError("vehicle on-ramp status lacks an exact assertion")
    for participant in record.participants:
        if not asserted(
            participant.evidence_ids,
            EvidenceAssertion.VEHICLE_HOLDER,
            participant.legal_name,
        ):
            raise ValueError("vehicle holder or awardee lacks exact evidence")
    access_assertions = {
        VehicleAccessPosture.DIRECT_HOLDER:
            EvidenceAssertion.VEHICLE_DIRECT_HOLDER,
        VehicleAccessPosture.TEAMING_REQUIRED:
            EvidenceAssertion.VEHICLE_TEAMING_REQUIRED,
        VehicleAccessPosture.CHANNEL_OR_RESELLER:
            EvidenceAssertion.VEHICLE_CHANNEL_OR_RESELLER,
    }
    access_assertion = access_assertions.get(record.access_posture)
    if access_assertion is not None and not asserted(
        record.access_evidence_ids,
        access_assertion,
        binding.client_name,
    ):
        raise ValueError("vehicle access posture lacks exact client evidence")
    if record.partner_route is not None and not asserted(
        record.partner_route.evidence_ids,
        EvidenceAssertion.VEHICLE_PARTNER_ROUTE,
        record.partner_route.partner_name,
    ):
        raise ValueError("vehicle partner route lacks exact evidence")

    activity_groups = (
        (record.notice_activity, {EvidenceKind.NOTICE}, False),
        (record.order_activity, {EvidenceKind.AWARD}, True),
        (record.award_activity, {EvidenceKind.AWARD}, True),
    )
    for activities, allowed_kinds, relationship_required in activity_groups:
        for activity in activities:
            activity_refs = tuple(
                evidence_by_id[evidence_id]
                for evidence_id in activity.evidence_ids
            )
            if not any(
                ref.source_kind in allowed_kinds
                and ref.source_identity.canonical_key
                == activity.source_identity.canonical_key
                for ref in activity_refs
            ):
                raise ValueError(
                    "vehicle notice, order, or award identity lacks exact lineage"
                )
            related = activity.related_vehicle_identity
            if relationship_required and related is None:
                raise ValueError(
                    "order or award activity lacks a related vehicle identity"
                )
            if related is None:
                continue
            expected_related = (
                record.parent_idv_source_identity
                or record.vehicle_source_identity
            )
            if expected_related is None \
                    or related.canonical_key != expected_related.canonical_key:
                raise ValueError(
                    "vehicle activity is not anchored to this record's exact "
                    "parent IDV or vehicle source identity"
                )
            if not any(
                ref.source_kind in allowed_kinds
                and ref.source_identity.canonical_key
                == activity.source_identity.canonical_key
                and any(
                    span.assertion
                    is EvidenceAssertion.PARENT_IDV_RELATIONSHIP
                    and _source_text_names_identifier(
                        span.quote,
                        activity.source_identity.record_id,
                    )
                    and _source_text_names_identifier(
                        span.quote,
                        related.record_id,
                    )
                    for span in ref.assertion_spans
                )
                for ref in activity_refs
            ):
                raise ValueError(
                    "vehicle activity relationship lacks an exact parent or "
                    "vehicle lineage assertion"
                )
    for timing in record.dates:
        timing_refs = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in timing.evidence_ids
        )
        if not evidence_supports_date_value(timing, timing_refs):
            raise ValueError("vehicle watch date is not visible in exact evidence")
    if record.ceiling_or_value is not None:
        value_refs = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in record.ceiling_or_value.evidence_ids
        )
        allowed_money_sources = {
            MoneyBasis.OBLIGATED_TO_DATE: {EvidenceKind.AWARD},
            MoneyBasis.AWARD_CEILING: {EvidenceKind.AWARD, EvidenceKind.NOTICE},
            MoneyBasis.PUBLISHED_EXACT_ESTIMATE: {
                EvidenceKind.NOTICE,
                EvidenceKind.AGENCY_FORECAST,
            },
            MoneyBasis.PUBLISHED_RANGE: {
                EvidenceKind.NOTICE,
                EvidenceKind.AGENCY_FORECAST,
            },
            MoneyBasis.BUDGET_REQUEST: set(),
            MoneyBasis.AGGREGATE_HISTORY: {EvidenceKind.AWARD},
            MoneyBasis.ANALYST_PIPELINE_VALUE: set(),
        }[record.ceiling_or_value.basis]
        if not any(ref.source_kind in allowed_money_sources for ref in value_refs):
            raise ValueError("vehicle money basis lacks compatible official evidence")

    coverage_rows: list[CoverageRecord] = []
    for query_id in record.coverage_query_ids:
        coverage = coverage_by_query_id.get(query_id)
        if coverage is None:
            raise ValueError("vehicle watch references unknown coverage query")
        if (coverage.client_id, coverage.run_id, coverage.scope_sha256) != (
            binding.client_id,
            binding.run_id,
            binding.scope_sha256,
        ):
            raise ValueError("vehicle watch coverage belongs to another client")
        coverage_rows.append(coverage)
    covered_evidence_ids = {
        evidence_id
        for coverage in coverage_rows
        for evidence_id in coverage.accepted_evidence_ids
    }
    if not set(record.official_evidence_ids).issubset(covered_evidence_ids):
        raise ValueError(
            "vehicle watch evidence is absent from its source-coverage receipt"
        )


class EventKind(str, Enum):
    PROCUREMENT_MILESTONE = "procurement_milestone"
    INDUSTRY_EVENT = "industry_event"
    CONFERENCE = "conference"
    BUDGET_MILESTONE = "budget_milestone"


class EventLifecycleStatus(str, Enum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"
    COMPLETED = "completed"
    DATE_TBD = "date_tbd"


class EventRecord(_FrozenContract):
    event_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_sha256: str
    title: str = Field(min_length=1)
    kind: EventKind
    timing: Optional[DateValue] = None
    organizer: Optional[str] = None
    agency: Optional[str] = None
    location: Optional[str] = None
    virtual: bool = False
    audience: Optional[str] = None
    relevance: str = Field(min_length=1)
    validate_next: str = Field(min_length=1)
    registration_url: Optional[HttpUrl] = None
    agenda_url: Optional[HttpUrl] = None
    venue_url: Optional[HttpUrl] = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    registration_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    agenda_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    venue_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    attending_agencies: tuple[str, ...] = Field(default_factory=tuple)
    attendance_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    flagship: bool = False
    flagship_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    lifecycle_status: EventLifecycleStatus = EventLifecycleStatus.SCHEDULED
    last_checked_at: datetime
    active: bool = True

    @model_validator(mode="after")
    def _event_fields_are_explicit(self) -> "EventRecord":
        _validate_hash(self.scope_sha256, "event scope_sha256")
        _unique(self.evidence_ids, "event evidence IDs")
        _unique(self.registration_evidence_ids,
                "event registration evidence IDs")
        _unique(self.agenda_evidence_ids, "event agenda evidence IDs")
        _unique(self.venue_evidence_ids, "event venue evidence IDs")
        _unique(tuple(value.casefold() for value in self.attending_agencies),
                "event attending agencies")
        _unique(self.attendance_evidence_ids, "attendance evidence IDs")
        _unique(self.flagship_evidence_ids, "event flagship evidence IDs")
        _require_aware(self.last_checked_at, "event last_checked_at")
        if self.validate_next.casefold() == self.relevance.casefold():
            raise ValueError(
                "event validation action must be separate from relevance"
            )
        role_links = (
            ("registration", self.registration_url,
             self.registration_evidence_ids),
            ("agenda", self.agenda_url, self.agenda_evidence_ids),
            ("venue", self.venue_url, self.venue_evidence_ids),
        )
        for label, value, evidence_ids in role_links:
            if value is not None and urlparse(str(value)).scheme != "https":
                raise ValueError("event links must use HTTPS")
            if bool(value) != bool(evidence_ids):
                raise ValueError(
                    f"event {label} link and evidence must be supplied together"
                )
        inactive_statuses = {
            EventLifecycleStatus.CANCELLED,
            EventLifecycleStatus.POSTPONED,
            EventLifecycleStatus.COMPLETED,
            EventLifecycleStatus.DATE_TBD,
        }
        if self.lifecycle_status in inactive_statuses and self.active:
            raise ValueError("inactive event lifecycle cannot remain active")
        if self.lifecycle_status == EventLifecycleStatus.SCHEDULED:
            if not self.active:
                raise ValueError("scheduled event must remain active")
            if self.timing is None:
                raise ValueError("scheduled event requires timing")
        if self.lifecycle_status == EventLifecycleStatus.DATE_TBD \
                and self.timing is not None:
            raise ValueError("date-TBD event cannot carry synthetic timing")
        event_date_kinds = {
            DateKind.EVENT_START,
            DateKind.EVENT_END,
            DateKind.INDUSTRY_DAY,
        }
        procurement_date_kinds = {
            DateKind.RESPONSE_DEADLINE,
            DateKind.QA_DEADLINE,
            DateKind.SITE_VISIT,
            DateKind.INDUSTRY_DAY,
            DateKind.FORECAST_SOLICITATION,
            DateKind.FORECAST_AWARD,
            DateKind.PROGRAM_DEADLINE,
            DateKind.CONFIRMED_RECOMPETE,
            DateKind.AWARD_END_RESEARCH_CLOCK,
            DateKind.MONITOR_DATE,
        }
        if self.kind in (EventKind.CONFERENCE, EventKind.INDUSTRY_EVENT):
            if not self.organizer or not self.location or not self.audience:
                raise ValueError(
                    "conference and industry events require organizer, "
                    "location, and audience"
                )
            if self.timing is not None:
                if self.timing.kind not in event_date_kinds:
                    raise ValueError(
                        "event timing kind does not match an event record"
                    )
                if self.timing.precision not in (
                    DatePrecision.DAY, DatePrecision.RANGE,
                ):
                    raise ValueError(
                        "scheduled events require an exact day or official range"
                    )
        elif self.kind == EventKind.PROCUREMENT_MILESTONE:
            if self.timing is None \
                    or self.timing.kind not in procurement_date_kinds:
                raise ValueError(
                    "procurement milestone uses an incompatible timing kind"
                )
        elif self.kind == EventKind.BUDGET_MILESTONE:
            if self.timing is None \
                    or self.timing.kind != DateKind.BUDGET_MILESTONE:
                raise ValueError("budget event requires budget-milestone timing")
        if bool(self.attending_agencies) != bool(self.attendance_evidence_ids):
            raise ValueError(
                "attendance claims and attendance evidence must be supplied together"
            )
        if self.flagship:
            if self.kind not in (EventKind.CONFERENCE, EventKind.INDUSTRY_EVENT):
                raise ValueError("only an industry event can be a flagship event")
            if not self.flagship_evidence_ids:
                raise ValueError("flagship event requires explicit evidence")
        elif self.flagship_evidence_ids:
            raise ValueError("non-flagship event cannot carry flagship evidence")
        assert_candidate_review_language(
            self.title,
            self.organizer or "",
            self.agency or "",
            self.location or "",
            self.audience or "",
            self.relevance,
            self.validate_next,
            *self.attending_agencies,
        )
        return self

    @property
    def all_evidence_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((
            *self.evidence_ids,
            *self.registration_evidence_ids,
            *self.agenda_evidence_ids,
            *self.venue_evidence_ids,
            *self.attendance_evidence_ids,
            *self.flagship_evidence_ids,
            *(self.timing.evidence_ids if self.timing is not None else ()),
        )))


class CoverageState(str, Enum):
    RETURNED = "returned"
    RETURNED_FALLBACK = "returned_fallback"
    PARTIAL = "partial"
    PARTIAL_FALLBACK = "partial_fallback"
    CURRENT_SNAPSHOT = "current_snapshot"
    CURRENT_FALLBACK_SNAPSHOT = "current_fallback_snapshot"
    STALE_SNAPSHOT = "stale_snapshot"
    FAILED = "failed"
    NOT_RUN = "not_run"
    SCOPE_EXCLUDED = "scope_excluded"
    NOT_USED = "not_used"


class CoverageRecord(_FrozenContract):
    client_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_sha256: str
    source: str = Field(min_length=1)
    query_family: str = Field(min_length=1)
    query_required: bool = True
    state: CoverageState
    window_start: date
    window_end: date
    attempted_at: Optional[datetime] = None
    records_returned: int = Field(default=0, ge=0)
    records_accepted: int = Field(default=0, ge=0)
    accepted_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    query_manifest_id: str = Field(min_length=1)
    query_id: Optional[str] = Field(default=None, min_length=1)
    watch_target_id: Optional[str] = Field(default=None, min_length=1)
    public_detail: str = ""

    @model_validator(mode="after")
    def _coverage_is_a_census_not_a_guess(self) -> "CoverageRecord":
        _validate_hash(self.scope_sha256, "coverage scope_sha256")
        if self.watch_target_id is not None and self.query_id is None:
            raise ValueError(
                "target-level coverage requires its originating query_id"
            )
        if self.window_end < self.window_start:
            raise ValueError("coverage window ends before it starts")
        _unique(self.accepted_evidence_ids, "coverage accepted evidence IDs")
        if self.records_accepted > self.records_returned:
            raise ValueError("accepted coverage records exceed returned records")
        if self.records_accepted != len(self.accepted_evidence_ids):
            raise ValueError(
                "accepted coverage count must close against evidence identities"
            )
        if self.state == CoverageState.FAILED \
                and (self.records_returned or self.records_accepted):
            raise ValueError("failed coverage cannot carry usable result counts")
        not_attempted = {
            CoverageState.NOT_RUN,
            CoverageState.SCOPE_EXCLUDED,
            CoverageState.NOT_USED,
        }
        if self.state in not_attempted:
            if self.attempted_at is not None:
                raise ValueError("unattempted source cannot carry attempted_at")
            if self.records_returned or self.records_accepted:
                raise ValueError("unattempted source cannot carry result counts")
        elif self.attempted_at is None:
            raise ValueError("attempted coverage state requires attempted_at")
        if self.attempted_at is not None:
            _require_aware(self.attempted_at, "coverage attempted_at")
        if re.search(
            r"(?:traceback|api[_ -]?key|authorization:|bearer\s+[a-z0-9._-]+)",
            self.public_detail,
            re.IGNORECASE,
        ):
            raise ValueError("coverage public detail contains sensitive internals")
        return self

    @property
    def identity_key(self) -> str:
        return "\x00".join((
            self.source.casefold(),
            self.query_family.casefold(),
            (self.query_id or "").casefold(),
            (self.watch_target_id or "").casefold(),
        ))


class EvidenceBoundClaim(_FrozenContract):
    block_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    records_show: str = Field(min_length=1)
    may_suggest: str = Field(min_length=1)
    validate_next: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _claim_is_three_part_and_bound(self) -> "EvidenceBoundClaim":
        _unique(self.evidence_ids, "claim evidence IDs")
        assert_candidate_review_language(
            self.heading,
            self.records_show,
            self.may_suggest,
            self.validate_next,
        )
        return self


class SearchConcepts(_FrozenContract):
    client_id: str = Field(min_length=1)
    keywords: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    naics_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    psc_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _concepts_are_unique_and_typed(self) -> "SearchConcepts":
        _unique(tuple(value.casefold() for value in self.keywords), "keywords")
        _unique(self.naics_codes, "NAICS codes")
        _unique(self.psc_codes, "PSC codes")
        _unique(self.evidence_ids, "search-concept evidence IDs")
        if any(re.fullmatch(r"[0-9]{6}", code) is None for code in self.naics_codes):
            raise ValueError("NAICS codes must contain six digits")
        return self


class ExecutionFramework(_FrozenContract):
    client_id: str = Field(min_length=1)
    assess: str = Field(min_length=1)
    target: str = Field(min_length=1)
    execute: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _copy_is_not_a_pipeline_control(self) -> "ExecutionFramework":
        _unique(self.evidence_ids, "execution evidence IDs")
        assert_candidate_review_language(self.assess, self.target, self.execute)
        return self


class TickerItem(_FrozenContract):
    ticker_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source_evidence_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    candidate_ids: tuple[str, ...] = Field(default_factory=tuple)
    historical_context: bool = False

    @model_validator(mode="after")
    def _ticker_is_traceable(self) -> "TickerItem":
        _unique(self.evidence_ids, "ticker evidence IDs")
        _unique(self.candidate_ids, "ticker candidate IDs")
        if self.source_evidence_id not in self.evidence_ids:
            raise ValueError("ticker source evidence must be in evidence_ids")
        assert_candidate_review_language(self.label)
        return self


class KpiKind(str, Enum):
    CANDIDATE_COUNT = "candidate_count"
    SOURCE_COVERAGE = "source_coverage"
    OBLIGATION = "obligation"
    AWARD_VALUE = "award_value"
    FORECAST_RANGE = "forecast_range"
    RESEARCH_SCALE = "research_scale"


class KpiTile(_FrozenContract):
    kpi_id: str = Field(min_length=1)
    kind: KpiKind
    eyebrow: str = Field(min_length=1)
    value: str = Field(min_length=1)
    title: str = Field(min_length=1)
    note: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    computed: bool = False
    money_id: Optional[str] = None

    @model_validator(mode="after")
    def _kpi_is_not_pipeline_value(self) -> "KpiTile":
        _unique(self.evidence_ids, "KPI evidence IDs")
        assert_candidate_review_language(
            self.eyebrow, self.value, self.title, self.note)
        if self.kind == KpiKind.CANDIDATE_COUNT and not self.computed:
            raise ValueError("candidate-count KPI must be computed")
        money_kinds = {
            KpiKind.OBLIGATION,
            KpiKind.AWARD_VALUE,
            KpiKind.FORECAST_RANGE,
            KpiKind.RESEARCH_SCALE,
        }
        if self.kind in money_kinds and not self.money_id:
            raise ValueError("money KPI requires a typed money identity")
        if self.kind not in money_kinds and self.money_id:
            raise ValueError("non-money KPI cannot carry a money identity")
        return self


class SectionSpec(_FrozenContract):
    ordinal: int = Field(ge=1, le=8)
    section_id: str = Field(min_length=1)
    heading: str = Field(min_length=1)


SECTION_ORDER = (
    SectionSpec(ordinal=1, section_id="forecast",
                heading="360° assessment: what the pattern suggests"),
    SectionSpec(ordinal=2, section_id="candidate-review",
                heading="Candidate opportunities for review"),
    SectionSpec(ordinal=3, section_id="signals",
                heading="Vehicle, partner, and market signals"),
    SectionSpec(ordinal=4, section_id="accounts",
                heading="Past awards and competitive analysis"),
    SectionSpec(ordinal=5, section_id="capabilities",
                heading="Preliminary keywords and capability search concepts"),
    SectionSpec(ordinal=6, section_id="calendar",
                heading="Federal opportunity calendar"),
    SectionSpec(ordinal=7, section_id="method",
                heading="Assess | Target | Execute"),
    SectionSpec(ordinal=8, section_id="evidence", heading="Evidence dock"),
)


class ProductSurface(str, Enum):
    COMMAND_CENTER_SCOREBOARD = "command_center_scoreboard"
    COMMAND_CENTER_TICKER = "command_center_ticker"
    ANALYST_LAYER = "analyst_layer"
    PRIMARY_RUN = "run_federal_opportunity_pre_assessment"
    REPORT_TICKER = "report_ticker"


class DocumentControl(str, Enum):
    EDIT = "toggle-edit"
    SAVE = "save-draft"
    RESET = "reset"
    DOWNLOAD_HTML = "download-html"
    PRINT_PDF = "print"


class ContextualEditorControl(str, Enum):
    UNDO_SOFT_DELETE = "undo-soft-delete"


class WorkflowAction(str, Enum):
    REFRESH_ANALYST_LAYER = "refresh-analyst-layer"


REQUIRED_PRODUCT_SURFACES = tuple(ProductSurface)
REQUIRED_DOCUMENT_CONTROLS = tuple(DocumentControl)
REQUIRED_CONTEXTUAL_EDITOR_CONTROLS = (
    ContextualEditorControl.UNDO_SOFT_DELETE,
)
REQUIRED_WORKFLOW_ACTIONS = (WorkflowAction.REFRESH_ANALYST_LAYER,)

FORBIDDEN_CONTROL_TOKENS = (
    "pursuit-dossier",
    "download-dossier",
    "re-screen",
    "rescreen",
    "re-search",
    "rerun",
    "re-run",
    "rescan",
    "re-scan",
    "promote-crm",
    "promote-pipeline",
)


class ProductControlContract(_FrozenContract):
    product_surfaces: tuple[ProductSurface, ...] = REQUIRED_PRODUCT_SURFACES
    document_controls: tuple[DocumentControl, ...] = REQUIRED_DOCUMENT_CONTROLS
    contextual_editor_controls: tuple[ContextualEditorControl, ...] = \
        REQUIRED_CONTEXTUAL_EDITOR_CONTROLS
    bottom_workflow_actions: tuple[WorkflowAction, ...] = REQUIRED_WORKFLOW_ACTIONS

    @model_validator(mode="after")
    def _inventory_is_exact(self) -> "ProductControlContract":
        if self.product_surfaces != REQUIRED_PRODUCT_SURFACES:
            raise ValueError("Candidate Review product surfaces must remain exact")
        if self.document_controls != REQUIRED_DOCUMENT_CONTROLS:
            raise ValueError("Candidate Review document controls must remain exact")
        if self.contextual_editor_controls != REQUIRED_CONTEXTUAL_EDITOR_CONTROLS:
            raise ValueError("Candidate Review requires contextual soft-delete Undo")
        if self.bottom_workflow_actions != REQUIRED_WORKFLOW_ACTIONS:
            raise ValueError(
                "Candidate Review requires exactly one Refresh from Analyst Layer action"
            )
        return self


class EditableTextSlot(_FrozenContract):
    edit_id: str = Field(min_length=1)
    baseline_text: str
    font_size_px: float = Field(ge=8, le=120)
    line_height: float = Field(ge=0.8, le=3)
    single_line: bool = False

    @model_validator(mode="after")
    def _baseline_obeys_line_contract(self) -> "EditableTextSlot":
        if self.single_line and ("\n" in self.baseline_text
                                 or "\r" in self.baseline_text):
            raise ValueError("single-line baseline text cannot contain newlines")
        return self


class AssetRole(str, Enum):
    CLIENT_LOGO = "client_logo"
    AGENCY_SEAL = "agency_seal"
    PARTNER_LOGO = "partner_logo"
    PRODUCT_LOGO = "product_logo"
    CARD_LOGO = "card_logo"


class AssetFit(str, Enum):
    CONTAIN = "contain"
    COVER = "cover"
    FILL = "fill"


class AssetSlot(_FrozenContract):
    asset_slot_id: str = Field(min_length=1)
    role: AssetRole
    entity_id: str = Field(min_length=1)
    alt_text: str = Field(min_length=1)
    baseline_src: str = Field(min_length=1)

    @model_validator(mode="after")
    def _slot_identity_is_semantic(self) -> "AssetSlot":
        if self.entity_id.casefold() not in self.asset_slot_id.casefold():
            raise ValueError("asset slot identity must contain its entity identity")
        if not self.baseline_src.startswith("data:image/"):
            raise ValueError("baseline report assets must be embedded data images")
        return self


class TextEditState(_FrozenContract):
    edit_id: str = Field(min_length=1)
    html_or_text: str
    font_size_px: float = Field(ge=8, le=120)
    line_height: float = Field(ge=0.8, le=3)


class AssetEditState(_FrozenContract):
    asset_slot_id: str = Field(min_length=1)
    src: str = Field(min_length=1)
    width_px: int = Field(ge=8, le=4096)
    height_px: int = Field(ge=8, le=4096)
    fit: AssetFit = AssetFit.CONTAIN
    position_x: float = Field(default=50, ge=0, le=100)
    position_y: float = Field(default=50, ge=0, le=100)
    deleted: bool = False

    @model_validator(mode="after")
    def _replacement_is_standalone(self) -> "AssetEditState":
        if not self.src.startswith("data:image/"):
            raise ValueError("edited report assets must be embedded data images")
        return self


class CandidateTombstone(_FrozenContract):
    candidate_id: str = Field(min_length=1)
    deleted_at: datetime
    undo_token: str = Field(min_length=1)
    prior_index: int = Field(ge=0)

    @model_validator(mode="after")
    def _deletion_time_is_aware(self) -> "CandidateTombstone":
        _require_aware(self.deleted_at, "candidate deletion timestamp")
        return self


class UndoState(_FrozenContract):
    token: str = Field(min_length=1)
    action: Literal["soft_delete"] = "soft_delete"
    candidate_id: str = Field(min_length=1)
    prior_order: tuple[str, ...] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _undo_is_replayable(self) -> "UndoState":
        _require_aware(self.created_at, "undo timestamp")
        _unique(self.prior_order, "undo prior order")
        if self.candidate_id not in self.prior_order:
            raise ValueError("undo candidate is absent from prior order")
        return self


class EditorState(_FrozenContract):
    client_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    baseline_document_sha256: str
    revision: int = Field(default=0, ge=0)
    text_edits: tuple[TextEditState, ...] = Field(default_factory=tuple)
    asset_edits: tuple[AssetEditState, ...] = Field(default_factory=tuple)
    candidate_order: tuple[str, ...] = Field(default_factory=tuple)
    tombstones: tuple[CandidateTombstone, ...] = Field(default_factory=tuple)
    undo: Optional[UndoState] = None

    @model_validator(mode="after")
    def _editor_state_is_deterministic(self) -> "EditorState":
        _validate_hash(self.baseline_document_sha256, "baseline_document_sha256")
        _unique(tuple(item.edit_id for item in self.text_edits), "text edit IDs")
        _unique(tuple(item.asset_slot_id for item in self.asset_edits),
                "asset edit IDs")
        _unique(self.candidate_order, "candidate order")
        _unique(tuple(item.candidate_id for item in self.tombstones),
                "candidate tombstones")
        if self.undo and self.undo.token not in {
            item.undo_token for item in self.tombstones
        }:
            raise ValueError("undo token has no matching tombstone")
        return self


CONTENT_BUDGETS = {
    "pattern_claims": 8,
    "candidates": 12,
    "vehicle_signals": 24,
    "market_signals": 12,
    "past_awards": 12,
    "calendar_events": 32,
    "ticker_items": 24,
    "kpi_tiles": 4,
    # 512, raised from 256 on 2026-07-29. apexanalytix matched 295 notices
    # and assembled 284 evidence records, which failed validation and killed
    # the press. Raising the envelope keeps every record; the alternative was
    # truncating to 256, and a silent cut of 28 records is exactly the
    # "screened X, showed Y" mislabel this system exists to prevent.
    "evidence_records": 512,
    # Vehicle-watch v2 can carry 1,024 exact SAM identities.  At the maximum
    # approved width it emits 4,609 target-level rows; the current standing
    # event census adds 300.  Keep one deliberate power-of-two envelope for
    # the complete combined audit receipt, never a sliced projection.
    "coverage_records": 8192,
    "text_slots": 1000,
    "asset_slots": 128,
}


class CandidateReviewDocument(_FrozenContract):
    """The only client-neutral source for the Candidate Review renderer."""

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    document_id: str = Field(min_length=1)
    baseline_document_sha256: str
    client_name: str = Field(min_length=1)
    binding: ArtifactBinding
    as_of: datetime
    generated_at: datetime
    sections: tuple[SectionSpec, ...] = SECTION_ORDER
    pattern_claims: tuple[EvidenceBoundClaim, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["pattern_claims"])
    candidates: tuple[CandidateUnit, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["candidates"])
    vehicle_watch_records: tuple[VehicleWatchRecord, ...] = Field(
        default_factory=tuple,
        max_length=CONTENT_BUDGETS["vehicle_signals"],
    )
    vehicle_signals: tuple[VehicleSignal, ...] = Field(
        default_factory=tuple,
        max_length=CONTENT_BUDGETS["vehicle_signals"],
    )
    market_signals: tuple[EvidenceBoundClaim, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["market_signals"])
    past_awards: tuple[EvidenceBoundClaim, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["past_awards"])
    search_concepts: Optional[SearchConcepts] = None
    calendar_events: tuple[EventRecord, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["calendar_events"])
    execution_framework: Optional[ExecutionFramework] = None
    evidence: tuple[EvidenceRecord, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["evidence_records"])
    coverage: tuple[CoverageRecord, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["coverage_records"])
    ticker_items: tuple[TickerItem, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["ticker_items"])
    kpi_tiles: tuple[KpiTile, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["kpi_tiles"])
    text_slots: tuple[EditableTextSlot, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["text_slots"])
    asset_slots: tuple[AssetSlot, ...] = Field(
        default_factory=tuple, max_length=CONTENT_BUDGETS["asset_slots"])
    editor_state: EditorState
    controls: ProductControlContract = Field(default_factory=ProductControlContract)

    @model_validator(mode="after")
    def _document_is_one_bound_evidence_universe(self) -> "CandidateReviewDocument":
        _require_aware(self.as_of, "document as_of")
        _require_aware(self.generated_at, "document generated_at")
        _validate_hash(self.baseline_document_sha256,
                       "document baseline_document_sha256")
        if self.generated_at > self.as_of:
            raise ValueError("document generation cannot postdate its as-of")
        if self.sections != SECTION_ORDER:
            raise ValueError("Candidate Review requires the exact eight sections in order")
        binding = self.binding
        if self.client_name != binding.client_name:
            raise ValueError("visible client name differs from artifact binding")
        if self.editor_state.client_id != binding.client_id \
                or self.editor_state.document_id != self.document_id:
            raise ValueError("editor state belongs to another client or document")
        if self.editor_state.baseline_document_sha256 \
                != self.baseline_document_sha256:
            raise ValueError("editor state belongs to another document baseline")

        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("evidence IDs must be unique")
        source_keys = [item.source_identity.canonical_key for item in self.evidence]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("evidence source identities must be unique")
        for item in self.evidence:
            if (item.client_id, item.run_id, item.scope_sha256) != (
                binding.client_id, binding.run_id, binding.scope_sha256,
            ):
                raise ValueError("cross-client, cross-run, or cross-scope evidence")
            if item.retrieved_at > self.as_of:
                raise ValueError("evidence retrieval postdates document as-of")
            if item.verified_at is not None and item.verified_at > self.as_of:
                raise ValueError("notice verification postdates document as-of")

        coverage_by_query_id: dict[str, CoverageRecord] = {}
        for coverage in self.coverage:
            if coverage.query_id is None:
                continue
            prior = coverage_by_query_id.setdefault(
                coverage.query_id,
                coverage,
            )
            if prior != coverage:
                raise ValueError("coverage query IDs must be unique")

        candidate_ids = tuple(item.candidate_id for item in self.candidates)
        _unique(candidate_ids, "candidate IDs")
        member_owners: dict[str, str] = {}
        money_by_id: dict[str, MoneyValue] = {}
        date_owners: dict[str, str] = {}
        for candidate in self.candidates:
            if candidate.client_id != binding.client_id:
                raise ValueError("candidate belongs to another client")
            for evidence_id in candidate.member_evidence_ids:
                prior = member_owners.setdefault(evidence_id, candidate.candidate_id)
                if prior != candidate.candidate_id:
                    raise ValueError(
                        "member evidence cannot belong to overlapping candidate units"
                    )
            self._require_refs(candidate.all_evidence_ids, evidence_by_id,
                               f"candidate {candidate.candidate_id}")
            candidate_money = list(candidate.money)
            if candidate.analyst_decision \
                    and candidate.analyst_decision.pipeline_value:
                candidate_money.append(candidate.analyst_decision.pipeline_value)
            for money in candidate_money:
                if money.money_id in money_by_id:
                    raise ValueError("document money IDs must be unique")
                money_by_id[money.money_id] = money
                if money.basis != MoneyBasis.ANALYST_PIPELINE_VALUE:
                    money_refs = [evidence_by_id[eid] for eid in money.evidence_ids]
                    allowed_money_sources = {
                        MoneyBasis.OBLIGATED_TO_DATE: {
                            EvidenceKind.AWARD, EvidenceKind.SUBAWARD},
                        MoneyBasis.AWARD_CEILING: {
                            EvidenceKind.AWARD, EvidenceKind.NOTICE},
                        MoneyBasis.PUBLISHED_EXACT_ESTIMATE: {
                            EvidenceKind.NOTICE, EvidenceKind.AGENCY_FORECAST},
                        MoneyBasis.PUBLISHED_RANGE: {
                            EvidenceKind.NOTICE, EvidenceKind.AGENCY_FORECAST},
                        MoneyBasis.BUDGET_REQUEST: {EvidenceKind.BUDGET},
                        MoneyBasis.AGGREGATE_HISTORY: {
                            EvidenceKind.AWARD, EvidenceKind.SUBAWARD},
                    }[money.basis]
                    if not any(ref.source_kind in allowed_money_sources
                               for ref in money_refs):
                        raise ValueError(
                            "typed money basis is not backed by a compatible source"
                        )
            for date_value in candidate.dates:
                prior = date_owners.setdefault(
                    date_value.date_id, f"candidate {candidate.candidate_id}"
                )
                if prior != f"candidate {candidate.candidate_id}":
                    raise ValueError("document date IDs must be globally unique")
            for member in candidate.members:
                member_records = [
                    evidence_by_id[eid]
                    for eid in member.member_evidence_ids
                ]
                if not any(record.can_anchor_candidate for record in member_records):
                    raise ValueError(
                        "news-only or context-only candidate units are not allowed"
                    )
                if not any(
                    record.source_identity.canonical_key
                    == member.source_identity.canonical_key
                    for record in member_records
                ):
                    raise ValueError("candidate member source identity is unresolved")
                if member.procurement_family \
                        and not member.procurement_family.fallback_notice_identity:
                    family = member.procurement_family
                    if not any(
                        record.source_kind == EvidenceKind.NOTICE
                        and (record.issuing_office or "").casefold()
                        == family.issuing_office.casefold()
                        and (record.solicitation_number or "").casefold()
                        == family.solicitation_number.casefold()
                        for record in member_records
                    ):
                        raise ValueError(
                            "named procurement family is not bound to member notice evidence"
                        )
                if candidate.kind == CandidateKind.CURRENT_NOTICE \
                        and not any(record.confirms_open_notice
                                    for record in member_records):
                    raise ValueError(
                        "current notice requires official primary SAM.gov notice evidence"
                    )
                if candidate.kind == CandidateKind.CURRENT_NOTICE:
                    active_notices = [
                        record for record in member_records
                        if record.confirms_open_notice
                    ]
                    if any(
                        record.verified_at is None
                        or self.as_of - record.verified_at > timedelta(hours=24)
                        for record in active_notices
                    ):
                        raise ValueError(
                            "current notice status must be verified within the as-of window"
                        )
            if candidate.lifecycle == LifecycleKind.CONFIRMED_RECOMPETE:
                confirmed = [
                    item for item in candidate.dates
                    if item.kind == DateKind.CONFIRMED_RECOMPETE
                    and item.status == DateStatus.CONFIRMED
                ]
                if not confirmed:
                    raise ValueError(
                        "confirmed recompete lifecycle requires an explicit confirmed date"
                    )
                refs = [
                    evidence_by_id[eid]
                    for item in confirmed
                    for eid in item.evidence_ids
                ]
                explicit_recompete_sources = {
                    EvidenceKind.NOTICE,
                    EvidenceKind.AGENCY_FORECAST,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                }
                if not any(
                    ref.source_kind in explicit_recompete_sources
                    and ref.official_source
                    and ref.primary_source
                    and ref.has_assertion(EvidenceAssertion.EXPLICIT_RECOMPETE)
                    for ref in refs
                ):
                    raise ValueError(
                        "confirmed recompete requires explicit official evidence"
                    )
            date_source_kinds = {
                DateKind.RESPONSE_DEADLINE: {EvidenceKind.NOTICE},
                DateKind.QA_DEADLINE: {EvidenceKind.NOTICE},
                DateKind.SITE_VISIT: {EvidenceKind.NOTICE},
                DateKind.INDUSTRY_DAY: {
                    EvidenceKind.NOTICE,
                    EvidenceKind.OFFICIAL_EVENT,
                    EvidenceKind.ORGANIZER_EVENT,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                },
                DateKind.FORECAST_SOLICITATION: {
                    EvidenceKind.AGENCY_FORECAST},
                DateKind.FORECAST_AWARD: {EvidenceKind.AGENCY_FORECAST},
                DateKind.PROGRAM_DEADLINE: {
                    EvidenceKind.NOTICE,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                    EvidenceKind.OFFICIAL_EVENT,
                },
                DateKind.CONFIRMED_RECOMPETE: {
                    EvidenceKind.NOTICE,
                    EvidenceKind.AGENCY_FORECAST,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                },
                DateKind.AWARD_END_RESEARCH_CLOCK: {EvidenceKind.AWARD},
                DateKind.BUDGET_MILESTONE: {EvidenceKind.BUDGET},
                DateKind.EVENT_START: {
                    EvidenceKind.OFFICIAL_EVENT,
                    EvidenceKind.ORGANIZER_EVENT,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                },
                DateKind.EVENT_END: {
                    EvidenceKind.OFFICIAL_EVENT,
                    EvidenceKind.ORGANIZER_EVENT,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                },
            }
            for date_value in candidate.dates:
                allowed = date_source_kinds.get(date_value.kind)
                if allowed is None:
                    continue
                refs = [evidence_by_id[eid] for eid in date_value.evidence_ids]
                if not any(ref.source_kind in allowed for ref in refs):
                    raise ValueError(
                        "typed date kind is not backed by a compatible source"
                    )
                if not evidence_supports_date_value(
                    date_value,
                    tuple(refs),
                ):
                    raise ValueError(
                        "typed date value is not visible in its evidence"
                    )
            if candidate.analyst_decision \
                    and candidate.analyst_decision.decided_at > self.as_of:
                raise ValueError("analyst decision postdates document as-of")

        if set(self.editor_state.candidate_order) != set(candidate_ids) \
                or len(self.editor_state.candidate_order) != len(candidate_ids):
            raise ValueError("editor candidate order must contain every candidate exactly once")
        tombstone_ids = {item.candidate_id for item in self.editor_state.tombstones}
        if not tombstone_ids.issubset(set(candidate_ids)):
            raise ValueError("editor tombstone references an unknown candidate")

        watch_record_ids = tuple(
            item.watch_record_id for item in self.vehicle_watch_records
        )
        _unique(watch_record_ids, "vehicle watch record IDs")
        watch_identity_keys: list[tuple[str, str]] = []
        for record in self.vehicle_watch_records:
            validate_vehicle_watch_record_evidence(
                record,
                binding=binding,
                as_of=self.as_of,
                evidence_by_id=evidence_by_id,
                coverage_by_query_id=coverage_by_query_id,
            )
            watch_identity_keys.append((
                record.managing_agency.casefold(),
                (
                    record.canonical_name
                    or record.vehicle_program_id
                    or record.parent_idv_piid
                    or ""
                ).casefold(),
            ))
            for timing in record.dates:
                prior = date_owners.setdefault(
                    timing.date_id,
                    f"vehicle watch {record.watch_record_id}",
                )
                if prior != f"vehicle watch {record.watch_record_id}":
                    raise ValueError("document date IDs must be globally unique")
            if record.ceiling_or_value is not None:
                money = record.ceiling_or_value
                if money.money_id in money_by_id:
                    raise ValueError("document money IDs must be unique")
                money_by_id[money.money_id] = money
        if len(watch_identity_keys) != len(set(watch_identity_keys)):
            raise ValueError("duplicate vehicle watch records must be consolidated")

        signal_ids = tuple(item.signal_id for item in self.vehicle_signals)
        _unique(signal_ids, "vehicle signal IDs")
        relationship_ids = tuple(
            item.relationship.relationship_id
            for item in self.vehicle_signals
            if item.relationship is not None
        )
        _unique(relationship_ids, "vehicle relationship IDs")
        vehicle_identity_cores: dict[str, tuple[str, Optional[str], Optional[str]]] = {}
        for signal in self.vehicle_signals:
            validate_vehicle_signal_evidence(
                signal,
                binding=binding,
                as_of=self.as_of,
                evidence_by_id=evidence_by_id,
                candidate_ids=candidate_ids,
            )
            identity_core = (
                signal.vehicle.agency.casefold(),
                signal.vehicle.name.casefold() if signal.vehicle.name else None,
                signal.vehicle.idv_piid.casefold()
                if signal.vehicle.idv_piid else None,
            )
            prior_identity = vehicle_identity_cores.setdefault(
                signal.vehicle.vehicle_id,
                identity_core,
            )
            if prior_identity != identity_core:
                raise ValueError(
                    "one vehicle ID carries conflicting agency, name, or PIID"
                )
            for timing in signal.dates:
                prior = date_owners.setdefault(
                    timing.date_id,
                    f"vehicle signal {signal.signal_id}",
                )
                if prior != f"vehicle signal {signal.signal_id}":
                    raise ValueError("document date IDs must be globally unique")

        claim_groups = (self.pattern_claims, self.market_signals, self.past_awards)
        block_ids = tuple(block.block_id for group in claim_groups for block in group)
        _unique(block_ids, "claim block IDs")
        for group in claim_groups:
            for block in group:
                if block.client_id != binding.client_id:
                    raise ValueError("claim block belongs to another client")
                self._require_refs(block.evidence_ids, evidence_by_id,
                                   f"claim {block.block_id}")

        if self.search_concepts:
            if self.search_concepts.client_id != binding.client_id:
                raise ValueError("search concepts belong to another client")
            self._require_refs(self.search_concepts.evidence_ids, evidence_by_id,
                               "search concepts")
        if self.execution_framework:
            if self.execution_framework.client_id != binding.client_id:
                raise ValueError("execution framework belongs to another client")
            self._require_refs(self.execution_framework.evidence_ids,
                               evidence_by_id, "execution framework")

        event_ids = tuple(item.event_id for item in self.calendar_events)
        _unique(event_ids, "event IDs")
        event_semantic_owners: dict[tuple[str, ...], str] = {}
        for event in self.calendar_events:
            if (event.client_id, event.run_id, event.scope_sha256) != (
                binding.client_id, binding.run_id, binding.scope_sha256,
            ):
                raise ValueError("event belongs to another client, run, or scope")
            self._require_refs(event.all_evidence_ids, evidence_by_id,
                               f"event {event.event_id}")
            if event.last_checked_at > self.as_of:
                raise ValueError("event verification postdates document as-of")
            newest_event_evidence = max(
                evidence_by_id[evidence_id].retrieved_at
                for evidence_id in event.all_evidence_ids
            )
            if event.last_checked_at < newest_event_evidence:
                raise ValueError(
                    "event last-checked time predates accepted evidence"
                )
            validate_event_watch_freshness(event, as_of=self.as_of)
            if event.timing is not None:
                prior = date_owners.setdefault(
                    event.timing.date_id, f"event {event.event_id}"
                )
                if prior != f"event {event.event_id}":
                    raise ValueError("document date IDs must be globally unique")
            refs = [evidence_by_id[eid] for eid in event.evidence_ids]
            allowed_event_sources = {
                EventKind.CONFERENCE: {
                    EvidenceKind.OFFICIAL_EVENT,
                    EvidenceKind.ORGANIZER_EVENT,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                },
                EventKind.INDUSTRY_EVENT: {
                    EvidenceKind.OFFICIAL_EVENT,
                    EvidenceKind.ORGANIZER_EVENT,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                    EvidenceKind.NOTICE,
                },
                EventKind.PROCUREMENT_MILESTONE: {
                    EvidenceKind.NOTICE,
                    EvidenceKind.AGENCY_FORECAST,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                    EvidenceKind.AWARD,
                    EvidenceKind.OFFICIAL_EVENT,
                    EvidenceKind.ORGANIZER_EVENT,
                },
                EventKind.BUDGET_MILESTONE: {
                    EvidenceKind.BUDGET,
                    EvidenceKind.AGENCY_ANNOUNCEMENT,
                },
            }[event.kind]
            if not any(
                ref.source_kind in allowed_event_sources
                and ref.official_source
                and ref.primary_source
                for ref in refs
            ):
                raise ValueError(
                    "event requires official organizer or agency evidence"
                )
            role_links = (
                (event.registration_url, event.registration_evidence_ids,
                 "registration"),
                (event.agenda_url, event.agenda_evidence_ids, "agenda"),
                (event.venue_url, event.venue_evidence_ids, "venue"),
            )
            for url, role_ids, label in role_links:
                if url is None:
                    continue
                role_refs = [evidence_by_id[eid] for eid in role_ids]
                normalized_url = str(url).rstrip("/")
                if not any(
                    ref.official_source
                    and ref.primary_source
                    and str(ref.source_url).rstrip("/") == normalized_url
                    for ref in role_refs
                ):
                    raise ValueError(
                        f"event {label} link is not bound to authoritative evidence"
                    )
            if event.attending_agencies:
                attendance_refs = [
                    evidence_by_id[eid]
                    for eid in event.attendance_evidence_ids
                ]
                for agency in event.attending_agencies:
                    if not any(
                        ref.official_source
                        and ref.primary_source
                        and any(
                            span.assertion == EvidenceAssertion.AGENCY_ATTENDANCE
                            and agency.casefold() in span.quote.casefold()
                            for span in ref.assertion_spans
                        )
                        for ref in attendance_refs
                    ):
                        raise ValueError(
                            "agency attendance requires an exact official "
                            "agenda assertion naming the agency"
                        )
            if event.kind in (EventKind.CONFERENCE, EventKind.INDUSTRY_EVENT) \
                    and not any(
                        ref.has_assertion(EvidenceAssertion.OFFICIAL_EVENT)
                        for ref in refs
                    ):
                raise ValueError(
                    "conference or industry event requires an exact official event assertion"
                )
            if event.flagship:
                flagship_refs = [
                    evidence_by_id[eid] for eid in event.flagship_evidence_ids
                ]
                if not any(
                    ref.official_source
                    and ref.primary_source
                    and ref.has_assertion(EvidenceAssertion.EVENT_FLAGSHIP)
                    for ref in flagship_refs
                ):
                    raise ValueError(
                        "flagship status requires an exact official assertion"
                    )
            lifecycle_assertions = {
                EventLifecycleStatus.CANCELLED:
                    EvidenceAssertion.EVENT_CANCELLED,
                EventLifecycleStatus.POSTPONED:
                    EvidenceAssertion.EVENT_POSTPONED,
                EventLifecycleStatus.DATE_TBD:
                    EvidenceAssertion.EVENT_DATE_TBD,
            }
            lifecycle_assertion = lifecycle_assertions.get(
                event.lifecycle_status)
            if lifecycle_assertion is not None and not any(
                evidence_by_id[eid].official_source
                and evidence_by_id[eid].primary_source
                and evidence_by_id[eid].has_assertion(lifecycle_assertion)
                for eid in event.evidence_ids
            ):
                raise ValueError(
                    "inactive event lifecycle requires exact official evidence"
                )
            if event.timing is not None:
                event_date_source_kinds = {
                    DateKind.RESPONSE_DEADLINE: {EvidenceKind.NOTICE},
                    DateKind.QA_DEADLINE: {EvidenceKind.NOTICE},
                    DateKind.SITE_VISIT: {EvidenceKind.NOTICE},
                    DateKind.INDUSTRY_DAY: {
                        EvidenceKind.NOTICE,
                        EvidenceKind.OFFICIAL_EVENT,
                        EvidenceKind.ORGANIZER_EVENT,
                        EvidenceKind.AGENCY_ANNOUNCEMENT,
                    },
                    DateKind.FORECAST_SOLICITATION: {
                        EvidenceKind.AGENCY_FORECAST},
                    DateKind.FORECAST_AWARD: {
                        EvidenceKind.AGENCY_FORECAST},
                    DateKind.PROGRAM_DEADLINE: {
                        EvidenceKind.NOTICE,
                        EvidenceKind.AGENCY_ANNOUNCEMENT,
                        EvidenceKind.OFFICIAL_EVENT,
                    },
                    DateKind.CONFIRMED_RECOMPETE: {
                        EvidenceKind.NOTICE,
                        EvidenceKind.AGENCY_FORECAST,
                        EvidenceKind.AGENCY_ANNOUNCEMENT,
                    },
                    DateKind.AWARD_END_RESEARCH_CLOCK: {
                        EvidenceKind.AWARD},
                    DateKind.BUDGET_MILESTONE: {EvidenceKind.BUDGET},
                    DateKind.EVENT_START: {
                        EvidenceKind.OFFICIAL_EVENT,
                        EvidenceKind.ORGANIZER_EVENT,
                        EvidenceKind.AGENCY_ANNOUNCEMENT,
                    },
                    DateKind.EVENT_END: {
                        EvidenceKind.OFFICIAL_EVENT,
                        EvidenceKind.ORGANIZER_EVENT,
                        EvidenceKind.AGENCY_ANNOUNCEMENT,
                    },
                    DateKind.MONITOR_DATE: {
                        EvidenceKind.NOTICE,
                        EvidenceKind.AGENCY_FORECAST,
                        EvidenceKind.AWARD,
                        EvidenceKind.BUDGET,
                        EvidenceKind.AGENCY_ANNOUNCEMENT,
                    },
                }
                timing_refs = [
                    evidence_by_id[eid] for eid in event.timing.evidence_ids
                ]
                allowed_timing_sources = event_date_source_kinds[
                    event.timing.kind]
                if not any(
                    ref.source_kind in allowed_timing_sources
                    and ref.official_source
                    and ref.primary_source
                    for ref in timing_refs
                ):
                    raise ValueError(
                        "calendar timing is not backed by compatible "
                        "official evidence"
                    )
                if not evidence_supports_date_value(
                    event.timing,
                    tuple(timing_refs),
                ):
                    raise ValueError(
                        "calendar timing value is not visible in its evidence"
                    )
                event_end = event.timing.end or event.timing.start \
                    or event.timing.sort_date
                if event.active and event_end < self.as_of.date():
                    raise ValueError("past events cannot remain active")
            semantic_key = (
                (event.organizer or event.agency or "").casefold(),
                re.sub(r"\W+", " ", event.title.casefold()).strip(),
                event.timing.source_text.casefold()
                if event.timing is not None else "date-tbd",
                (event.location or "").casefold(),
                str(event.virtual),
            )
            prior_event = event_semantic_owners.setdefault(
                semantic_key, event.event_id)
            if prior_event != event.event_id:
                raise ValueError(
                    "semantically duplicate calendar events must be clustered"
                )

        calendar_sort_keys = tuple(
            (
                event.timing.sort_date if event.timing is not None
                else date.max,
                event.timing.sort_datetime.isoformat()
                if event.timing is not None
                and event.timing.sort_datetime is not None else "",
                event.event_id,
            )
            for event in self.calendar_events
        )
        if calendar_sort_keys != tuple(sorted(calendar_sort_keys)):
            raise ValueError("calendar events must be in deterministic date order")

        ticker_ids = tuple(item.ticker_id for item in self.ticker_items)
        _unique(ticker_ids, "ticker IDs")
        for item in self.ticker_items:
            self._require_refs(item.evidence_ids, evidence_by_id,
                               f"ticker {item.ticker_id}")
            if any(cid not in candidate_ids for cid in item.candidate_ids):
                raise ValueError("ticker references an unknown candidate")
        _unique(tuple(item.kpi_id for item in self.kpi_tiles), "KPI IDs")
        for item in self.kpi_tiles:
            self._require_refs(item.evidence_ids, evidence_by_id,
                               f"KPI {item.kpi_id}")
            if item.money_id:
                money = money_by_id.get(item.money_id)
                if money is None:
                    raise ValueError("KPI references an unknown money identity")
                if money.basis == MoneyBasis.ANALYST_PIPELINE_VALUE:
                    raise ValueError("KPI cannot publish analyst pipeline value")
                if not set(money.evidence_ids).issubset(set(item.evidence_ids)):
                    raise ValueError(
                        "KPI money identity is not backed by its visible evidence"
                    )
                if item.value != money.display_value:
                    raise ValueError(
                        "KPI money display must be derived from its typed money value"
                    )

        text_ids = tuple(item.edit_id for item in self.text_slots)
        asset_ids = tuple(item.asset_slot_id for item in self.asset_slots)
        _unique(text_ids, "editable text IDs")
        _unique(asset_ids, "asset slot IDs")
        if any(item.edit_id not in set(text_ids)
               for item in self.editor_state.text_edits):
            raise ValueError("text edit references an unknown editable text slot")
        text_slots_by_id = {item.edit_id: item for item in self.text_slots}
        if any(
            text_slots_by_id[item.edit_id].single_line
            and ("\n" in item.html_or_text or "\r" in item.html_or_text)
            for item in self.editor_state.text_edits
        ):
            raise ValueError("single-line text edit cannot contain newlines")
        if any(item.asset_slot_id not in set(asset_ids)
               for item in self.editor_state.asset_edits):
            raise ValueError("asset edit references an unknown asset slot")

        coverage_keys = tuple(item.identity_key for item in self.coverage)
        _unique(coverage_keys, "source coverage rows")
        for item in self.coverage:
            if (item.client_id, item.run_id, item.scope_sha256) != (
                binding.client_id, binding.run_id, binding.scope_sha256,
            ):
                raise ValueError(
                    "coverage belongs to another client, run, or scope"
                )
            if item.attempted_at is not None and item.attempted_at > self.as_of:
                raise ValueError("coverage attempt postdates document as-of")
            if not (item.window_start <= self.as_of.date() <= item.window_end):
                raise ValueError("coverage window does not contain document as-of")
            self._require_refs(
                item.accepted_evidence_ids,
                evidence_by_id,
                f"coverage {item.source}/{item.query_family}",
            )
        assert_candidate_review_language(self.client_name)
        return self

    @staticmethod
    def _require_refs(
        values: tuple[str, ...],
        evidence_by_id: dict[str, EvidenceRecord],
        label: str,
    ) -> None:
        missing = [value for value in values if value not in evidence_by_id]
        if missing:
            raise ValueError(f"{label} has unresolved evidence references: {missing}")

    @property
    def canonical_evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.evidence)
