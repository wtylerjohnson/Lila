"""Live collection providers for the Candidate Review watch (Phase 3, v1).

SAM.gov and USAspending lanes run live; GSA eLibrary, agency program
forecasts, restricted exports, and the event lanes return typed NOT_RUN
coverage until their adapters land (never a silent zero).  Zero LLM.  SAM
calls honor the shared api.data.gov daily budget through ``sam_quota``.

Every authoritative payload fetched during collection is stashed and
persisted into the digest-bound generation through ``extra_artifacts``, so
the document press replays the exact records with the same-run ``as_of``
(the 24h current-notice window fix).  Timestamps on live leads and fetched
records are quantized to the run ``as_of``: collection is one atomic
observation instant, and every freshness window derives from it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from agents.candidate_review_v1.contracts import (
    NoticeRole,
    NoticeStatus,
    SourceIdentity,
)
from agents.candidate_review_v1.generation_fetchers import (
    VEHICLE_RECORDS_ARTIFACT,
    vehicle_records_payload,
)
from agents.candidate_review_v1.pipeline import (
    ProviderBinding,
    ProviderMode,
    ResearchProviderBindings,
)
from agents.candidate_review_v1.vehicle_watch import (
    VehicleLead,
    VehicleQuerySpec,
    VehicleSearchResponse,
    VehicleTargetKind,
    VehicleWatchLane,
)
from agents.candidate_review_v1.verification import FetchedRecord

LIVE_PROVIDER_VERSION = "candidate_review_v1.live.v1"

_MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November",
                "December")
_ON_RAMP_TITLE_RE = re.compile(r"on[- ]?ramp", re.I)
_VEHICLE_NAME_RE = re.compile(
    r"^(?P<name>[A-Z][\w+.& -]{2,60}?)\s+(?:GWAC|IDIQ|BPA|Schedule)?\s*"
    r"on[- ]?ramp", re.I)
_ESTABLISH_RE = re.compile(r"\b(establish|new)\b.*\bIDIQ\b|\bIDIQ\b.*\b(establish|new)\b", re.I)


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _spoken_date(value: Optional[str]) -> Optional[tuple[str, date]]:
    """ISO-ish date string to the published 'Month D, YYYY' protocol form."""

    if not value:
        return None
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return f"{_MONTH_NAMES[day.month - 1]} {day.day}, {day.year}", day


def _notice_role_for(title: str) -> NoticeRole:
    if _ON_RAMP_TITLE_RE.search(title or ""):
        return NoticeRole.VEHICLE_ON_RAMP
    if _ESTABLISH_RE.search(title or ""):
        return NoticeRole.VEHICLE_ESTABLISHMENT
    return NoticeRole.END_USER_REQUIREMENT


def _vehicle_line(title: str) -> str:
    match = _VEHICLE_NAME_RE.match(title or "")
    if match is None:
        return ""
    return f"Vehicle: {match.group('name').strip()}. "


@dataclass
class LiveCollectionRuntime:
    """Searchers plus the collection-time fetch stash for one live run."""

    bindings: ResearchProviderBindings
    event_searcher: Optional[Callable]
    vehicle_searcher: Callable
    records: dict[str, FetchedRecord] = field(default_factory=dict)

    def extra_artifacts(self) -> dict[str, object]:
        if not self.records:
            return {}
        return {VEHICLE_RECORDS_ARTIFACT: vehicle_records_payload(self.records)}


class _LiveVehicleSearcher:
    """Translate vehicle query specs into official API calls, lane by lane."""

    def __init__(self, *, binding, as_of: datetime, sam_source,
                 award_fetch: Callable[[str], dict],
                 quota_guard: Callable[[str], bool],
                 records: dict[str, FetchedRecord],
                 sweep_loader: Optional[Callable[[], tuple]] = None) -> None:
        self._binding = binding
        self._as_of = as_of
        self._sam = sam_source
        self._award_fetch = award_fetch
        self._quota_guard = quota_guard
        self._records = records
        self._award_memo: dict[str, Optional[dict]] = {}
        self._sweep_loader = sweep_loader
        self._sweep_cache: Optional[tuple] = None

    def _sweep_rows(self) -> tuple:
        """(rows, snapshot_at) from the designated sweep; ((), None) if absent."""
        if self._sweep_cache is None:
            rows, snapshot_at = (), None
            if self._sweep_loader is not None:
                try:
                    rows, snapshot_at = self._sweep_loader()
                except Exception:  # noqa: BLE001 - absent sweep, typed lanes
                    rows, snapshot_at = (), None
            self._sweep_cache = (tuple(rows or ()), snapshot_at)
        return self._sweep_cache

    def __call__(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        lane = query.lane
        if lane == VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES:
            return self._sam_lane(query)
        if lane in (VehicleWatchLane.USASPENDING_IDV,
                    VehicleWatchLane.USASPENDING_ORDER_LINEAGE):
            return self._usaspending_lane(query)
        return VehicleSearchResponse(
            state="not_run",
            public_detail=(
                f"The {lane.value} live adapter is not wired yet; the lane "
                "is recorded as not run."))

    # -- SAM.gov ----------------------------------------------------------

    def _sam_lane(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        if query.target_kind == VehicleTargetKind.CANDIDATE_SOURCE:
            return VehicleSearchResponse(
                state="not_run",
                public_detail=(
                    "By-id notice confirmation is not wired yet; active "
                    "notices re-enter through the term and NAICS lanes."))
        if self._sam is None or not self._quota_guard(
                "candidate-review-watch"):
            return self._sam_sweep_screen(query)
        from tools.api.base import SourceQuery

        value = (query.target_value or "").strip()
        source_query = SourceQuery(
            keywords=[value]
            if query.target_kind == VehicleTargetKind.CLIENT_TERM and value
            else [],
            naics_codes=[value]
            if query.target_kind == VehicleTargetKind.NAICS and value else [],
            psc_codes=[value]
            if query.target_kind == VehicleTargetKind.PSC and value else [],
            agencies=[value]
            if query.target_kind == VehicleTargetKind.AGENCY and value else [],
            posted_from=self._as_of.date() - timedelta(days=364),
            posted_to=self._as_of.date(),
            deadline_from=self._as_of.date(),
            limit=50,
        )
        rows = self._sam.search(source_query)
        leads: list[VehicleLead] = []
        seen: set[str] = set()
        for row in rows:
            notice_id = str(getattr(row, "source_id", "") or "")
            if not notice_id or notice_id in seen:
                continue
            seen.add(notice_id)
            url = str(getattr(row, "api_url", "") or "")
            if notice_id not in url or not url.startswith("https://"):
                url = f"https://sam.gov/opp/{notice_id}/view"
            title = str(getattr(row, "title", "") or notice_id)
            lead = VehicleLead(
                lead_id=f"live-sam-{query.query_id}-{notice_id}",
                client_id=self._binding.client_id,
                run_id=self._binding.run_id,
                scope_sha256=self._binding.scope_sha256,
                query_id=query.query_id,
                lane=VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
                discovered_at=self._as_of,
                title=title,
                source_url=url,
                source_identity=SourceIdentity(
                    source_system="sam.gov", record_id=notice_id))
            leads.append(lead)
            self._stash_sam_record(lead, row)
        if not leads:
            fallback = self._sam_sweep_screen(query)
            if fallback.records_returned:
                return fallback
        return VehicleSearchResponse(
            state="returned", records_returned=len(leads), leads=leads,
            public_detail=(
                f"SAM.gov returned {len(leads)} active notice(s) for the "
                "approved frame."))

    def _sam_sweep_screen(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        """Screen the sweep's extract-backed official SAM rows, quota-free.

        The daily extract is the designed discovery spine (the whole notice
        universe, screened locally); these rows are official SAM.gov records
        carried by the gate-designated sweep, so the lane reports RETURNED
        with its provenance named instead of a not-run apology.
        """

        rows, snapshot_at = self._sweep_rows()
        if not rows or snapshot_at is None:
            return VehicleSearchResponse(
                state="not_run",
                public_detail=(
                    "No SAM budget and no extract-backed sweep rows were "
                    "available to screen."))
        value = (query.target_value or "").strip().casefold()
        if not value:
            return VehicleSearchResponse(
                state="not_run",
                public_detail=(
                    "The extract screen needs a term, code, or agency from "
                    "the approved frame."))

        def _matches(row: dict) -> bool:
            kind = query.target_kind
            if kind == VehicleTargetKind.CLIENT_TERM:
                raw = row.get("raw_payload") or {}
                hay = ((row.get("title") or "") + " "
                       + str(raw.get("description") or "")).casefold()
                return value in hay
            if kind == VehicleTargetKind.NAICS:
                return str(row.get("naics_code") or "").startswith(value)
            if kind == VehicleTargetKind.PSC:
                return str(row.get("psc_code") or "").startswith(value)
            if kind == VehicleTargetKind.AGENCY:
                return value in str(row.get("agency") or "").casefold()
            return False

        leads: list[VehicleLead] = []
        for row in rows:
            if len(leads) >= 8:
                break
            notice_id = str(row.get("source_id") or "")
            if not notice_id or not _matches(row):
                continue
            deadline = _spoken_date(row.get("response_deadline"))
            if deadline is not None and deadline[1] < self._as_of.date():
                continue
            url = str(row.get("api_url") or "")
            if notice_id not in url or not url.startswith("https://"):
                url = f"https://sam.gov/opp/{notice_id}/view"
            title = str(row.get("title") or notice_id)
            lead = VehicleLead(
                lead_id=f"live-sam-sweep-{query.query_id}-{notice_id}",
                client_id=self._binding.client_id,
                run_id=self._binding.run_id,
                scope_sha256=self._binding.scope_sha256,
                query_id=query.query_id,
                lane=VehicleWatchLane.SAM_CONTRACT_OPPORTUNITIES,
                discovered_at=self._as_of,
                title=title,
                source_url=url,
                source_identity=SourceIdentity(
                    source_system="sam.gov", record_id=notice_id))
            leads.append(lead)
            key = lead.source_identity.canonical_key
            if key in self._records:
                continue
            raw = dict(row.get("raw_payload") or {})
            agency = str(row.get("agency") or "").strip()
            description = " ".join(str(
                raw.get("description")
                or "The official notice describes the requirement.")
                .split())[:800]
            parts = []
            if agency:
                parts.append(f"Agency: {agency}.")
            vehicle_line = _vehicle_line(title)
            if vehicle_line:
                parts.append(vehicle_line.strip())
            parts.append(f"{title}.")
            parts.append(description if description.endswith(".")
                         else description + ".")
            if deadline is not None:
                parts.append(f"Responses are due {deadline[0]}.")
            # The snapshot instant is both retrieval and verification:
            # the record bytes date from the extract, not from this read.
            self._records[key] = FetchedRecord(
                lead_id=lead.lead_id,
                final_url=url,
                fetched_at=snapshot_at,
                title=title,
                text=" ".join(parts),
                record_sha256=_digest(
                    raw or {"notice": notice_id, "title": title}),
                verified_at=snapshot_at,
                notice_status=NoticeStatus.ACTIVE,
                notice_role=_notice_role_for(title),
                issuing_office=agency or None,
                solicitation_number=(
                    str(raw.get("solicitationNumber")
                        or raw.get("solicitation") or "") or None),
            )
        return VehicleSearchResponse(
            state="returned", records_returned=len(leads), leads=leads,
            public_detail=(
                f"Screened the official daily-extract snapshot in the "
                f"designated sweep (quota-free); {len(leads)} notice(s) "
                "matched this lane."))

    def _stash_sam_record(self, lead: VehicleLead, row) -> None:
        key = lead.source_identity.canonical_key
        if key in self._records:
            return
        raw = dict(getattr(row, "raw_payload", None) or {})
        agency = str(getattr(row, "agency", "") or "").strip()
        title = lead.title
        deadline = _spoken_date(getattr(row, "response_deadline", None))
        description = " ".join(str(
            raw.get("description") or "The official notice describes the "
            "requirement and timing.").split())[:800]
        parts = []
        if agency:
            parts.append(f"Agency: {agency}.")
        vehicle_line = _vehicle_line(title)
        if vehicle_line:
            parts.append(vehicle_line.strip())
        parts.append(f"{title}.")
        parts.append(description if description.endswith(".")
                     else description + ".")
        if deadline is not None:
            parts.append(f"Responses are due {deadline[0]}.")
        active_flag = str(raw.get("active", "")).strip().casefold()
        status = (NoticeStatus.ACTIVE if active_flag in ("yes", "true", "")
                  else NoticeStatus.CLOSED)
        self._records[key] = FetchedRecord(
            lead_id=lead.lead_id,
            final_url=str(lead.source_url),
            fetched_at=self._as_of,
            title=title,
            text=" ".join(parts),
            record_sha256=_digest(raw or {"notice": lead.title}),
            verified_at=self._as_of,
            notice_status=status,
            notice_role=_notice_role_for(title),
            issuing_office=agency or None,
            solicitation_number=(
                str(raw.get("solicitationNumber") or "") or None),
        )

    # -- USAspending ------------------------------------------------------

    def _usaspending_lane(self, query: VehicleQuerySpec) -> VehicleSearchResponse:
        identity = query.target_source_identity
        if identity is None:
            return VehicleSearchResponse(
                state="not_run",
                public_detail=(
                    "The award lane requires an exact source identity from "
                    "the approved frame; none was supplied."))
        gid = identity.record_id
        if gid in self._award_memo:
            payload = self._award_memo[gid]
        else:
            from tools.api.award_repull import AWARD_ENDPOINT
            try:
                payload = self._award_fetch(AWARD_ENDPOINT.format(gid=gid))
            except Exception:  # noqa: BLE001 - typed failure, no internals
                payload = None
            self._award_memo[gid] = payload
        if not isinstance(payload, dict):
            return VehicleSearchResponse(
                state="failed",
                public_detail=(
                    "The official award endpoint did not return this record "
                    "this run."))
        piid = str(payload.get("piid") or payload.get("fain") or gid)
        parent = payload.get("parent_award") or {}
        parent_piid = str(parent.get("piid") or "") or None
        parent_gid = str(parent.get("generated_unique_award_id") or "") or None
        awarding = payload.get("awarding_agency") or {}
        funding = payload.get("funding_agency") or {}
        agency = str(
            ((awarding.get("toptier_agency") or {}).get("name"))
            or ((awarding.get("subtier_agency") or {}).get("name"))
            or awarding.get("name")
            or ((funding.get("toptier_agency") or {}).get("name"))
            or "").strip()
        end = _spoken_date(
            ((payload.get("period_of_performance") or {}).get("end_date")))
        description = " ".join(str(
            payload.get("description")
            or "The official award record describes the services.")
            .split())[:600]
        url = f"https://www.usaspending.gov/award/{gid}"
        source_identity = SourceIdentity(
            source_system="usaspending.gov", record_id=gid)
        lineage = query.lane == VehicleWatchLane.USASPENDING_ORDER_LINEAGE
        if lineage and parent_gid is None:
            return VehicleSearchResponse(
                state="returned", records_returned=0,
                public_detail=(
                    "The award record names no parent IDV; the lineage lane "
                    "returned no usable lead."))
        parent_identity = (
            SourceIdentity(source_system="usaspending.gov",
                           record_id=parent_gid)
            if lineage else source_identity)
        lead = VehicleLead(
            lead_id=f"live-usa-{query.query_id}-{gid}",
            client_id=self._binding.client_id,
            run_id=self._binding.run_id,
            scope_sha256=self._binding.scope_sha256,
            query_id=query.query_id,
            lane=query.lane,
            discovered_at=self._as_of,
            title=f"{piid} award record",
            source_url=url,
            source_identity=source_identity,
            parent_idv_identity=parent_identity,
            order_identity=source_identity if lineage else None)
        key = source_identity.canonical_key
        if key not in self._records:
            parts = []
            if agency:
                parts.append(f"Agency: {agency}.")
            parts.append(description if description.endswith(".")
                         else description + ".")
            if lineage and parent_piid:
                parts.append(
                    f"Task order {piid} on award record {gid} uses "
                    f"parent IDV {parent_piid} under parent award "
                    f"record {parent_gid}.")
            elif not lineage:
                parts.append(f"The parent IDV {piid} covers these services.")
            if end is not None:
                parts.append(f"The period of performance ends {end[0]}.")
            self._records[key] = FetchedRecord(
                lead_id=lead.lead_id,
                final_url=url,
                fetched_at=self._as_of,
                title=lead.title,
                text=" ".join(parts),
                record_sha256=_digest(payload),
            )
        return VehicleSearchResponse(
            state="returned", records_returned=1, leads=(lead,),
            public_detail="The official award record returned one lead.")


def load_live_provider_runtime(
    *,
    binding,
    as_of: datetime,
    root=None,
    sam_source=None,
    award_fetch: Optional[Callable[[str], dict]] = None,
    quota_guard: Optional[Callable[[str], bool]] = None,
    sweep_loader: Optional[Callable[[], tuple]] = None,
) -> LiveCollectionRuntime:
    """Build the LIVE runtime; seams are injectable for offline tests.

    Defaults: ``SamGovSource`` with the ``SAM_GOV_API_KEY`` environment key
    (a missing key makes the SAM lane a typed NOT_RUN, never a crash),
    ``tools.api._http.get_json`` for award detail, and the shared
    ``sam_quota.guard``.
    """

    if sam_source is None:
        # ZERO-SAM PRESS (operator spec, 2026-08-03): the press path spends
        # no metered SAM quota unless the operator explicitly enables it.
        # With the flag off the SAM lane runs _sam_sweep_screen, the
        # quota-free extract-backed screen, and reports its provenance.
        from tools.api.sam_quota import press_live_sam_enabled
        if press_live_sam_enabled():
            from tools.api.sam_gov import SamGovSource
            candidate = SamGovSource()
            sam_source = candidate if candidate._api_key else None
    if award_fetch is None:
        from tools.api._http import get_json
        award_fetch = get_json
    if quota_guard is None:
        from tools.api import sam_quota
        quota_guard = sam_quota.guard
    if sweep_loader is None:
        def sweep_loader():
            import json as _json
            from pathlib import Path as _Path

            from agents.review import sweep_artifact_path
            base = _Path(root) if root is not None else _Path(__file__)                .resolve().parents[2]
            sweep_path = _Path(sweep_artifact_path(
                binding.client_name,
                review_dir=str(base / "data" / "review")))
            sweep = _json.loads(sweep_path.read_text(encoding="utf-8"))
            generated = datetime.fromisoformat(
                str(sweep.get("generated_at")))
            return ((sweep.get("results") or {}).get("sam.gov") or (),
                    generated)

    records: dict[str, FetchedRecord] = {}
    vehicle_searcher = _LiveVehicleSearcher(
        binding=binding, as_of=as_of, sam_source=sam_source,
        award_fetch=award_fetch, quota_guard=quota_guard, records=records,
        sweep_loader=sweep_loader)
    config_sha256 = _digest({
        "version": LIVE_PROVIDER_VERSION,
        "sam_key_configured": sam_source is not None,
        "lanes_live": ["sam_contract_opportunities", "usaspending_idv",
                       "usaspending_order_lineage"],
    })
    bindings = ResearchProviderBindings(
        event=ProviderBinding(
            adapter_name="lila-live-event",
            adapter_version=LIVE_PROVIDER_VERSION,
            config_sha256=config_sha256,
            mode=ProviderMode.UNAVAILABLE),
        vehicle=ProviderBinding(
            adapter_name="lila-live-vehicle",
            adapter_version=LIVE_PROVIDER_VERSION,
            config_sha256=config_sha256,
            mode=ProviderMode.LIVE),
    )
    runtime = LiveCollectionRuntime(
        bindings=bindings,
        event_searcher=None,
        vehicle_searcher=vehicle_searcher,
        records=records,
    )
    return runtime
