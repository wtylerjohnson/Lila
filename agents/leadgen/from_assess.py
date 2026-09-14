"""AssessRun + target_actions projection -> draft LeadRows.

Pure mapper. No persistence, no LLM, no Apollo, no ``targeting_rules``
import. Opportunity assessment is the parent. LeadRow is the child.

Drafts start ``WATCH`` or ``HOLD`` until sales-ready gates exist. This
shim never emits ``LEAD_T1`` or ``LEAD_T2``. Targeting motion ``rule_id``
``T1``/``T2``/``T3`` stays on ``BuyingMotion.targeting_rule_id`` and is
never stored as ``lead_tier``.

Missing pathway, seller route, clock, or permission holds the row with
an explicit promotion condition. The mapper does not invent a pathway
kind, a prime route, or a communication permission.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from agents.assess.contracts import (
    AssessRun,
    CoverageStatus,
    EvidenceKind,
    EvidenceRef,
    EvidenceTier,
    IntelligenceStatus,
    LifecycleStage,
    LiveClassification,
    LiveRecommendation,
    LiveSolicitation,
    OpportunityThesis,
    PartnerOpportunity,
    ProjectedWindow,
    SourceCoverage,
)

from ._base import SCHEMA_VERSION, _FrozenContract
from .contracts import LeadRow, OpportunityAssessment
from .enums import (
    TIER_READINESS,
    AssessmentSubjectKind,
    CommercialMotionKind,
    CommunicationPermission,
    Contactability,
    LeadTier,
    NextActionVerb,
    PathwayKind,
    SellerPathKind,
    TargetingRuleId,
)
from .ids import compose_assessment_id, compose_lead_id, compose_trace_id
from .motion import BuyingMotion
from .next_action import CurrentNextAction
from .pathway import ActionableExternalPathway, PublishedContact
from .seller_path import SellerTransactionPath
from .traces import DecisionTrace

CLOCK_UNESTABLISHED = "clock unestablished"
GATES_NOT_READY = (
    "sales-ready gates do not exist; do not auto-promote to "
    "lead T1 or lead T2"
)
ROUTE_HOLD = (
    "seller route unestablished: bind a PartnerOpportunity "
    "direction or a stated paper-holder route_role before promotion"
)
CLOCK_HOLD = (
    "clock unestablished: bind a response deadline or dated "
    "period_end before promotion"
)
PATHWAY_HOLD = (
    "pathway unestablished: bind a published SAM notice, agency "
    "forecast, vehicle, or rulemaking evidence URL before promotion"
)
PERMISSION_HOLD = (
    "communication permission is none: sales-ready gates do not exist; "
    "do not auto-promote to lead T1 or lead T2"
)

_LIVE_NO_CHILD = (
    LiveClassification.EXCLUDED,
    LiveClassification.AWARDED_OR_CLOSED,
)
_CLASS_TO_STAGE = {
    LiveClassification.BID_NOW: LifecycleStage.LIVE_SOLICITATION,
    LiveClassification.AMENDMENT: LifecycleStage.LIVE_SOLICITATION,
    LiveClassification.MARKET_RESEARCH: LifecycleStage.MARKET_RESEARCH,
    LiveClassification.PRESOLICITATION: LifecycleStage.PRESOLICITATION,
    LiveClassification.SPECIAL_NOTICE: LifecycleStage.ACQUISITION_PLANNING,
    LiveClassification.AWARDED_OR_CLOSED: LifecycleStage.AWARD,
    LiveClassification.EXCLUDED: LifecycleStage.LIVE_SOLICITATION,
    LiveClassification.UNSCREENED: LifecycleStage.LIVE_SOLICITATION,
}
_ROW_TO_MOTION = {
    "renewal": CommercialMotionKind.RENEWAL,
    "displacement": CommercialMotionKind.DISPLACEMENT,
    "adjacency": CommercialMotionKind.ADJACENCY,
}
_ROW_TO_RULE = {
    "renewal": TargetingRuleId.T1,
    "displacement": TargetingRuleId.T2,
    "adjacency": TargetingRuleId.T3,
}
_EVIDENCE_TO_PATHWAY = {
    EvidenceKind.AGENCY_FORECAST: PathwayKind.AGENCY_FORECAST,
    EvidenceKind.REGULATION: PathwayKind.OFFICIAL_RULEMAKING,
    EvidenceKind.LEGISLATION: PathwayKind.OFFICIAL_RULEMAKING,
    EvidenceKind.VEHICLE: PathwayKind.VEHICLE_ORDERING,
}
_ROUTE_TO_PATH = {
    "reseller": SellerPathKind.CHANNEL_RESELLER,
    "channel_reseller": SellerPathKind.CHANNEL_RESELLER,
    "vehicle_holder": SellerPathKind.VEHICLE_ACCESS,
    "vehicle_access": SellerPathKind.VEHICLE_ACCESS,
}
_GOV_PUBLISHED = "government published"


class AssessLeadDrafts(_FrozenContract):
    """In-memory draft batch. Not a persistence envelope."""

    schema_version: str = SCHEMA_VERSION
    assess_run_id: str
    parents: tuple[OpportunityAssessment, ...]
    leads: tuple[LeadRow, ...]
    traces: tuple[DecisionTrace, ...]


def draft_lead_rows(
    assess: AssessRun | Mapping[str, Any],
    target_actions: Mapping[str, Any] | Sequence[Any] | None = None,
) -> AssessLeadDrafts:
    """Map one Assess run plus a target_actions projection to drafts.

    ``target_actions`` is the already-built projection dict (or a list of
    its rows). This function does not call ``build_target_actions`` or
    import targeting / Apollo modules.
    """

    run = coerce_assess_run(assess)
    projection = coerce_target_actions(target_actions)
    subjects = _index_subjects(run)
    parents = [
        _parent_from_subject(run, item) for item in subjects
    ]
    coverage_hold = _coverage_hold(run.coverage)
    partner_by_link = _partners_by_link(run)
    leads: list[LeadRow] = []
    traces: list[DecisionTrace] = []
    lead_ids_by_parent: dict[str, list[str]] = {
        parent.assessment_id: [] for parent in parents
    }

    for parent, item in zip(parents, subjects):
        kind, subject = item
        if kind == AssessmentSubjectKind.RESEARCH_SUBJECT:
            traces.append(_parent_trace(run, parent, "Research subject preserved without a child: buying decision, clock and eligible seller route remain to be established"))
            continue
        if kind == AssessmentSubjectKind.PARTNER_LINK:
            traces.append(_parent_trace(
                run, parent,
                "partner_link is a seller-path source; children mint "
                "under the linked live or thesis parent",
            ))
            continue
        drafts, draft_traces = _drafts_for_parent(
            run, parent, kind, subject, projection,
            partners=partner_by_link.get(parent.subject_id, ()),
            coverage_hold=coverage_hold,
        )
        leads.extend(drafts)
        traces.extend(draft_traces)
        lead_ids_by_parent[parent.assessment_id].extend(
            row.lead_id for row in drafts)

    bound_parents = tuple(
        parent.model_copy(update={
            "lead_ids": tuple(lead_ids_by_parent[parent.assessment_id]),
        })
        for parent in parents
    )
    return AssessLeadDrafts(
        assess_run_id=run.run_id,
        parents=bound_parents,
        leads=tuple(leads),
        traces=tuple(traces),
    )


def coerce_assess_run(assess: AssessRun | Mapping[str, Any]) -> AssessRun:
    """Accept an ``AssessRun`` or an AssessRun-shaped / golden-pack dict."""

    if isinstance(assess, AssessRun):
        return AssessRun.model_validate(assess.model_dump(mode="python"))
    if not isinstance(assess, Mapping):
        raise TypeError("assess must be an AssessRun or mapping")
    payload: Any = assess
    for key in ("assess_run", "run", "AssessRun"):
        inner = assess.get(key)
        if isinstance(inner, Mapping) and inner.get("run_id"):
            payload = inner
            break
    if payload is not assess and "schema_version" in assess:
        version = assess["schema_version"]
        from agents.assess.ledger import _contains_acquisition
        if type(version) is not int or version not in (1, 2, 3):
            raise ValueError("unsupported Assess envelope version")
        if not str(payload.get("run_id", "")).startswith(f"assess:v{version}:"):
            raise ValueError("Assess envelope version disagrees with its run identity")
        if version == 1 and _contains_acquisition(payload):
            raise ValueError("legacy Assess envelope cannot contain v2 source acquisition")
    return AssessRun.model_validate(payload)


def coerce_target_actions(
    projection: Mapping[str, Any] | Sequence[Any] | None,
) -> dict[str, Any]:
    """Normalize a target_actions projection. Empty is a valid input."""

    if projection is None:
        return {"rows": [], "groups": [], "motions": []}
    if isinstance(projection, Mapping):
        if any(key in projection for key in ("rows", "groups", "motions")):
            return dict(projection)
        inner = projection.get("target_actions")
        if isinstance(inner, Mapping):
            return dict(inner)
        if isinstance(inner, Sequence) and not isinstance(inner, (str, bytes)):
            return {"rows": list(inner), "groups": [], "motions": []}
        raise ValueError(
            "target_actions projection must carry rows, groups, or motions")
    if isinstance(projection, Sequence) and not isinstance(
            projection, (str, bytes)):
        return {"rows": list(projection), "groups": [], "motions": []}
    raise TypeError("target_actions must be a mapping, a row list, or None")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _index_subjects(
    run: AssessRun,
) -> list[tuple[AssessmentSubjectKind, Any]]:
    items: list[tuple[AssessmentSubjectKind, Any]] = []
    for record in run.live.records:
        items.append((AssessmentSubjectKind.LIVE_SOLICITATION, record))
    for thesis in run.horizon.items:
        items.append((AssessmentSubjectKind.DEVELOPING_THESIS, thesis))
    for partner in run.partners.items:
        items.append((AssessmentSubjectKind.PARTNER_LINK, partner))
    if run.research is not None:
        items.extend((AssessmentSubjectKind.RESEARCH_SUBJECT, subject) for subject in run.research.items)
    return items


def _parent_from_subject(
    run: AssessRun,
    item: tuple[AssessmentSubjectKind, Any],
) -> OpportunityAssessment:
    kind, subject = item
    if kind == AssessmentSubjectKind.LIVE_SOLICITATION:
        return _parent_from_live(run, subject)
    if kind == AssessmentSubjectKind.DEVELOPING_THESIS:
        return _parent_from_thesis(run, subject)
    if kind == AssessmentSubjectKind.RESEARCH_SUBJECT:
        from agents.assess.reviewed_cases import ReviewedSubject
        overlay = ReviewedSubject.model_validate_json(subject.reviewed_overlay_json) if subject.reviewed_overlay_json else None
        return OpportunityAssessment(
            assessment_id=compose_assessment_id(run.run_id,kind.value,subject.subject_id),
            assess_run_id=run.run_id, client_name=run.client_name,
            profile_version=run.profile_version, scope=run.scope, as_of=run.as_of,
            subject_kind=kind, subject_id=subject.subject_id, title=subject.title,
            agency=subject.agency, research_subject=subject,
            research=overlay.research if overlay else None,
            targets=overlay.targets if overlay else ())
    return _parent_from_partner(run, subject)


def _parent_from_live(
    run: AssessRun, record: LiveSolicitation,
) -> OpportunityAssessment:
    return OpportunityAssessment(
        assessment_id=compose_assessment_id(
            run.run_id, AssessmentSubjectKind.LIVE_SOLICITATION.value,
            record.record_id),
        assess_run_id=run.run_id,
        client_name=run.client_name,
        profile_version=run.profile_version,
        scope=run.scope,
        as_of=run.as_of,
        subject_kind=AssessmentSubjectKind.LIVE_SOLICITATION,
        subject_id=record.record_id,
        title=record.title,
        agency=record.agency,
        solicitation_number=record.solicitation_number,
        notice_id=record.notice_id,
        lifecycle=_CLASS_TO_STAGE.get(record.classification),
        live_classification=record.classification,
        live_recommendation=record.recommendation,
        requirement_span=record.requirement_excerpt,
    )


def _parent_from_thesis(
    run: AssessRun, thesis: OpportunityThesis,
) -> OpportunityAssessment:
    return OpportunityAssessment(
        assessment_id=compose_assessment_id(
            run.run_id, AssessmentSubjectKind.DEVELOPING_THESIS.value,
            thesis.thesis_id),
        assess_run_id=run.run_id,
        client_name=run.client_name,
        profile_version=run.profile_version,
        scope=run.scope,
        as_of=run.as_of,
        subject_kind=AssessmentSubjectKind.DEVELOPING_THESIS,
        subject_id=thesis.thesis_id,
        title=thesis.title,
        agency=thesis.agency,
        lifecycle=thesis.lifecycle_stage,
    )


def _parent_from_partner(
    run: AssessRun, partner: PartnerOpportunity,
) -> OpportunityAssessment:
    return OpportunityAssessment(
        assessment_id=compose_assessment_id(
            run.run_id, AssessmentSubjectKind.PARTNER_LINK.value,
            partner.partner_id),
        assess_run_id=run.run_id,
        client_name=run.client_name,
        profile_version=run.profile_version,
        scope=run.scope,
        as_of=run.as_of,
        subject_kind=AssessmentSubjectKind.PARTNER_LINK,
        subject_id=partner.partner_id,
        title=partner.partner_name,
        agency=_clean(partner.vehicle_or_channel) or "unstated agency",
    )


def _partners_by_link(
    run: AssessRun,
) -> dict[str, tuple[PartnerOpportunity, ...]]:
    grouped: dict[str, list[PartnerOpportunity]] = {}
    for partner in run.partners.items:
        for link in partner.linked_assess_ids:
            grouped.setdefault(link, []).append(partner)
    return {key: tuple(values) for key, values in grouped.items()}


def _coverage_hold(coverage: tuple[SourceCoverage, ...]) -> str | None:
    blockers = [
        row for row in coverage
        if row.required and row.status != CoverageStatus.COMPLETE
    ]
    if not blockers:
        return None
    named = ", ".join(
        f"{row.source} ({row.status.value})" for row in blockers)
    return (
        f"required Assess coverage is incomplete ({named}): "
        "refresh the named lane before promotion"
    )


def _drafts_for_parent(
    run: AssessRun,
    parent: OpportunityAssessment,
    kind: AssessmentSubjectKind,
    subject: Any,
    projection: Mapping[str, Any],
    *,
    partners: tuple[PartnerOpportunity, ...],
    coverage_hold: str | None,
) -> tuple[list[LeadRow], list[DecisionTrace]]:
    rows = _joined_action_rows(parent, subject, projection)
    units = _motion_units(rows, projection)
    if not units and not _should_mint_assess_child(kind, subject):
        return [], [_parent_trace(
            run, parent,
            "parent only: Assess recommendation or status does not "
            "mint a draft child",
        )]
    if not units:
        units = [_assess_derived_unit(kind, subject)]

    drafts: list[LeadRow] = []
    traces: list[DecisionTrace] = []
    seller = _seller_path(parent.subject_id, partners, units[0] if units else {})
    for unit in units:
        motion = _buying_motion(parent, kind, subject, unit)
        if motion is None:
            traces.append(_parent_trace(
                run, parent,
                "HOLD: retain parent research; no supported buying motion, "
                "buyer or clock establishes an actionable child",
            ))
            continue
        pathway = _pathway_from_parent(kind, subject, unit)
        if pathway is None:
            traces.append(_parent_trace(
                run, parent,
                f"HOLD: {PATHWAY_HOLD}",
            ))
            continue
        unit_seller = _seller_path(parent.subject_id, partners, unit)
        if unit_seller.kind != SellerPathKind.PATH_UNKNOWN:
            seller = unit_seller
        next_action, lead_tier = _next_action_and_tier(
            kind, subject, pathway, seller, motion, coverage_hold)
        lead_id = compose_lead_id(
            parent.assessment_id, motion.motion_id,
            pathway.pathway_id, seller.path_id)
        lead = LeadRow(
            lead_id=lead_id,
            parent_assessment_id=parent.assessment_id,
            assess_run_id=run.run_id,
            buying_motion=motion,
            external_pathway=pathway,
            seller_path=seller,
            next_action=next_action,
            lead_tier=lead_tier,
            readiness=TIER_READINESS[lead_tier],
            contactability=pathway.contactability,
            communication_permission=next_action.communication_permission,
            decision_trace_id=compose_trace_id(run.run_id, lead_id),
            targets=_targets_for_rows([
                r for r in rows
                if (_clean(r.get("motion_id")) or _clean(r.get("spec_id"))
                    or "motion-unspecified") == unit.get("motion_id")
            ], pathway),
        )
        drafts.append(lead)
        traces.append(_lead_trace(run, parent, lead, subject))
    return drafts, traces


def _targets_for_rows(rows, pathway):
    """Complete existing per-spec actions without inferring person authority."""
    from .targets import LeadTarget
    targets = {}
    for row in rows:
        if isinstance(row.get("target"), Mapping):
            target = LeadTarget.model_validate(row["target"])
        else:
            source = _clean(row.get("contact_source_class")).casefold()
            kind = {"government published": "government_published",
                    "commercial enrichment": "apollo",
                    "company published": "company_published"}.get(source)
            required = [row.get("name"), row.get("title"), row.get("target_organisation"),
                        row.get("source_url"), row.get("why_this_person"),
                        row.get("recommended_action"), row.get("route_role")]
            if not kind or not all(required):
                continue  # Incomplete legacy target is a research gap, not invented data.
            phones = row.get("phones") or []
            phone = row.get("phone") or next((p.get("number") for p in phones
                                              if isinstance(p, Mapping) and p.get("number")), None)
            target = LeadTarget(
                name=row["name"], role=row["title"], organization=row["target_organisation"],
                source_kind=kind, source_url=row["source_url"], email=row.get("email"), phone=phone,
                contact_status=_clean(row.get("email_status")) or "Contact status needs confirmation",
                route=row["route_role"], reason_to_contact=row["why_this_person"],
                next_ask=row["recommended_action"],
                authority_boundary="; ".join(row.get("cautions") or []) or (
                    "Published POC; follow current notice communication instructions" if kind == "government_published"
                    else "Role match does not establish account ownership, buying authority or outreach permission"),
                evidence=pathway.evidence)
        targets[(target.name, str(target.source_url))] = target
    return tuple(targets.values())


def _should_mint_assess_child(
    kind: AssessmentSubjectKind, subject: Any,
) -> bool:
    if kind == AssessmentSubjectKind.LIVE_SOLICITATION:
        if subject.classification in _LIVE_NO_CHILD:
            return False
        if subject.recommendation == LiveRecommendation.NO_BID:
            return False
        return not (
            subject.recommendation == LiveRecommendation.NONE
            and subject.classification != LiveClassification.UNSCREENED
        )
    if kind == AssessmentSubjectKind.DEVELOPING_THESIS:
        return subject.status not in (
            IntelligenceStatus.RETIRED, IntelligenceStatus.INVALIDATED)
    return False


def _assess_derived_unit(
    kind: AssessmentSubjectKind, subject: Any,
) -> dict[str, Any]:
    if kind == AssessmentSubjectKind.LIVE_SOLICITATION:
        return {
            "motion_id": f"m:{subject.record_id}:live_bid",
            "row_class": "",
            "rule_id": None,
        }
    return {
        "motion_id": f"m:{getattr(subject, 'thesis_id', 'subject')}:thesis",
        "row_class": "",
        "rule_id": None,
    }


def _joined_action_rows(
    parent: OpportunityAssessment,
    subject: Any,
    projection: Mapping[str, Any],
) -> list[dict[str, Any]]:
    keys = _subject_join_keys(parent, subject)
    joined: list[dict[str, Any]] = []
    for row in _iter_action_rows(projection):
        if not isinstance(row, Mapping):
            continue
        row_keys = _action_join_keys(row)
        if keys & row_keys:
            joined.append(dict(row))
    return joined


def _subject_join_keys(
    parent: OpportunityAssessment, subject: Any,
) -> set[str]:
    keys = {
        _norm_key(parent.subject_id),
        _norm_key(parent.notice_id),
        _norm_key(parent.solicitation_number),
    }
    if isinstance(subject, LiveSolicitation):
        keys.add(_norm_key(subject.record_id))
        keys.add(_norm_key(subject.notice_id))
        keys.add(_norm_key(subject.solicitation_number))
    elif isinstance(subject, OpportunityThesis):
        keys.add(_norm_key(subject.thesis_id))
    return {key for key in keys if key}


def _action_join_keys(row: Mapping[str, Any]) -> set[str]:
    keys = {
        _norm_key(row.get("notice_id")),
        _norm_key(row.get("solicitation_number")),
    }
    for record_id in row.get("record_ids") or []:
        keys.add(_norm_key(record_id))
    for url in row.get("record_urls") or []:
        notice = _notice_from_url(url)
        if notice:
            keys.add(_norm_key(notice))
        keys.add(_norm_key(url))
    source_url = _clean(row.get("source_url"))
    if source_url:
        notice = _notice_from_url(source_url)
        if notice:
            keys.add(_norm_key(notice))
    return {key for key in keys if key}


def _norm_key(value: Any) -> str:
    return _clean(value).casefold()


def _notice_from_url(url: Any) -> str | None:
    parsed = urlparse(_clean(url))
    host = (parsed.hostname or "").lower()
    if host != "sam.gov" and not host.endswith(".sam.gov"):
        return None
    segments = [
        unquote(part).strip()
        for part in parsed.path.split("/") if part.strip()
    ]
    lowered = [part.lower() for part in segments]
    if "opp" in lowered:
        idx = lowered.index("opp")
        if idx + 1 < len(segments):
            return segments[idx + 1]
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in {"noticeid", "notice_id", "id"}:
            for value in values:
                if _clean(value):
                    return _clean(value)
    return None


def _iter_action_rows(projection: Mapping[str, Any]) -> list[Any]:
    rows = list(projection.get("rows") or [])
    for group in projection.get("groups") or []:
        if isinstance(group, Mapping):
            rows.extend(group.get("rows") or [])
    # Preserve first-seen order without collapsing distinct motions.
    seen: set[str] = set()
    unique: list[Any] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        marker = json.dumps(row, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(row)
    return unique


def _motion_units(
    rows: list[dict[str, Any]],
    projection: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Dedup person-grain target_actions rows into opportunity-grain units."""

    units: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        motion_id = _clean(row.get("motion_id")) or _clean(row.get("spec_id"))
        if not motion_id:
            motion_id = "motion-unspecified"
        if motion_id not in units:
            units[motion_id] = {
                "motion_id": motion_id,
                "row_class": _clean(row.get("row_class")),
                "rule_id": _rule_id_from_row(row, projection),
                "buying_agency": _clean(row.get("buying_agency")),
                "route_role": _clean(row.get("route_role")),
                "period_end": _period_end_from_row(row),
                "notice_id": _clean(row.get("notice_id")),
                "source_url": _clean(row.get("source_url")),
                "published_contacts": _published_contacts(row),
                "why_now": _clean(row.get("why_now")),
                "recommended_action": _clean(row.get("recommended_action")),
                "why_this_account": _clean(row.get("why_this_account")),
            }
            order.append(motion_id)
        else:
            existing = units[motion_id]
            if not existing["rule_id"]:
                existing["rule_id"] = _rule_id_from_row(row, projection)
            existing["published_contacts"].extend(_published_contacts(row))
    return [units[key] for key in order]


def _rule_id_from_row(
    row: Mapping[str, Any], projection: Mapping[str, Any],
) -> str | None:
    raw = _clean(row.get("rule_id") or row.get("targeting_rule_id"))
    if raw:
        return raw
    motion_id = _clean(row.get("motion_id"))
    for motion in list(projection.get("motions") or []):
        if not isinstance(motion, Mapping):
            continue
        if motion_id and _clean(motion.get("motion_id")) != motion_id:
            continue
        for spec in motion.get("specs") or []:
            if isinstance(spec, Mapping) and _clean(spec.get("rule_id")):
                return _clean(spec.get("rule_id"))
    for group in projection.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        if motion_id and _clean(group.get("motion_id")) != motion_id:
            continue
        for spec in group.get("specs") or []:
            if isinstance(spec, Mapping) and _clean(spec.get("rule_id")):
                return _clean(spec.get("rule_id"))
    return None


def _period_end_from_row(row: Mapping[str, Any]) -> date | None:
    for key in ("period_end", "deadline", "anticipated_date"):
        parsed = _parse_date(row.get(key))
        if parsed:
            return parsed
    for record in row.get("join_records") or []:
        if isinstance(record, Mapping):
            parsed = _parse_date(record.get("period_end"))
            if parsed:
                return parsed
    return _parse_date_from_why_now(_clean(row.get("why_now")))


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_date_from_why_now(text: str) -> date | None:
    for token in text.replace(",", " ").split():
        parsed = _parse_date(token)
        if parsed:
            return parsed
    return None


def _published_contacts(row: Mapping[str, Any]) -> list[PublishedContact]:
    source = _clean(row.get("contact_source_class")).casefold()
    if source != _GOV_PUBLISHED:
        return []
    name = _clean(row.get("name"))
    if not name:
        return []
    url = _clean(row.get("source_url"))
    try:
        return [PublishedContact(
            name=name,
            title=_clean(row.get("title")) or None,
            source_url=url or None,
            email=_clean(row.get("email")) or None,
            phone=_clean(row.get("phone")) or None,
        )]
    except ValidationError:
        return [PublishedContact(name=name, title=_clean(row.get("title")) or None)]


def _targeting_rule_id(
    row_class: str, rule_id_raw: Any,
) -> TargetingRuleId | None:
    raw = _clean(rule_id_raw)
    if raw in {item.value for item in TargetingRuleId}:
        return TargetingRuleId(raw)
    return _ROW_TO_RULE.get(row_class.casefold())


def _buying_motion(
    parent: OpportunityAssessment,
    kind: AssessmentSubjectKind,
    subject: Any,
    unit: Mapping[str, Any],
) -> BuyingMotion | None:
    row_class = _clean(unit.get("row_class"))
    motion_kind = _ROW_TO_MOTION.get(row_class.casefold())
    if motion_kind is None:
        if (kind == AssessmentSubjectKind.LIVE_SOLICITATION
                and subject.classification not in {
                    LiveClassification.BID_NOW, LiveClassification.AMENDMENT}):
            # A market-research or unscreened census member is still a parent.
            # Its presence, a contact, or MONITOR is not a live buying event.
            return None
        motion_kind = (
            CommercialMotionKind.LIVE_BID
            if kind == AssessmentSubjectKind.LIVE_SOLICITATION
            else CommercialMotionKind.ADJACENCY
        )
    stage = parent.lifecycle or LifecycleStage.LIVE_SOLICITATION
    if isinstance(subject, OpportunityThesis):
        stage = subject.lifecycle_stage
    elif isinstance(subject, LiveSolicitation):
        stage = _CLASS_TO_STAGE.get(
            subject.classification, LifecycleStage.LIVE_SOLICITATION)
    buyer = (
        _clean(getattr(subject, "agency", None))
        or _clean(unit.get("buying_agency"))
        or _clean(parent.agency)
    )
    if not buyer:
        return None
    clock = None
    window = None
    if isinstance(subject, LiveSolicitation):
        clock = subject.response_deadline
        component = subject.component
        office = subject.office
    elif isinstance(subject, OpportunityThesis):
        clock = subject.projected_window.end or subject.projected_window.start
        window = subject.projected_window
        component = subject.component
        office = subject.office
    else:
        component = None
        office = None
    if clock is None:
        clock = unit.get("period_end") if isinstance(
            unit.get("period_end"), date) else _parse_date(unit.get("period_end"))
    if clock is None and window is None:
        window = ProjectedWindow(label=CLOCK_UNESTABLISHED)
    motion_id = _clean(unit.get("motion_id")) or f"m:{parent.subject_id}"
    try:
        return BuyingMotion(
            motion_id=motion_id,
            parent_subject_id=parent.subject_id,
            kind=motion_kind,
            stage=stage,
            buyer_agency=buyer,
            buyer_component=component,
            buyer_office=office,
            clock=clock,
            window=window,
            targeting_rule_id=_targeting_rule_id(
                row_class, unit.get("rule_id")),
            recorded_at=parent.as_of,
        )
    except ValidationError:
        return None


def _pathway_from_parent(
    kind: AssessmentSubjectKind,
    subject: Any,
    unit: Mapping[str, Any],
) -> ActionableExternalPathway | None:
    contacts = _unique_contacts(unit.get("published_contacts") or [])
    if isinstance(subject, LiveSolicitation):
        return _live_pathway(subject, contacts)
    evidence = tuple(getattr(subject, "evidence", ()) or ())
    if not evidence:
        return None
    notice_id = _clean(getattr(subject, "notice_id", None)) or _clean(
        unit.get("notice_id"))
    chosen_kind = _pathway_kind_for(evidence, notice_id)
    if chosen_kind is None:
        return None
    source_url = str(evidence[0].source_url)
    contactability = (
        Contactability.PUBLISHED_POC if contacts
        else Contactability.ORGANIZATION_ONLY
    )
    try:
        return ActionableExternalPathway(
            pathway_id=f"pw:{_subject_key(subject)}:{chosen_kind.value}",
            kind=chosen_kind,
            source_url=source_url,
            evidence=list(evidence),
            notice_id=notice_id or None,
            published_contacts=contacts,
            contactability=contactability,
        )
    except ValidationError:
        return None


def _live_pathway(
    record: LiveSolicitation,
    contacts: list[PublishedContact],
) -> ActionableExternalPathway | None:
    evidence = tuple(record.authoritative_evidence)
    if not evidence:
        return None
    current = next((item for item in evidence
                    if item.tier == EvidenceTier.NOTICE
                    and item.kind == EvidenceKind.NOTICE
                    and item.primary_source
                    and (_notice_from_url(item.source_url) or "").casefold()
                    == record.notice_id.strip().casefold()), None)
    if current is None:
        return None
    contactability = (
        Contactability.PUBLISHED_POC if contacts
        else Contactability.ORGANIZATION_ONLY
    )
    try:
        return ActionableExternalPathway(
            pathway_id=f"pw:{record.record_id}:{PathwayKind.SAM_NOTICE.value}",
            kind=PathwayKind.SAM_NOTICE,
            source_url=str(current.source_url),
            evidence=list(evidence),
            notice_id=record.notice_id,
            published_contacts=contacts,
            contactability=contactability,
        )
    except ValidationError:
        return None


def _pathway_kind_for(
    evidence: tuple[EvidenceRef, ...], notice_id: str,
) -> PathwayKind | None:
    for item in evidence:
        if (item.tier == EvidenceTier.NOTICE
                and item.kind == EvidenceKind.NOTICE
                and item.primary_source
                and notice_id):
            return PathwayKind.SAM_NOTICE
        mapped = _EVIDENCE_TO_PATHWAY.get(item.kind)
        if mapped is not None:
            return mapped
    return None


def _subject_key(subject: Any) -> str:
    for attr in ("record_id", "thesis_id", "partner_id"):
        value = _clean(getattr(subject, attr, None))
        if value:
            return value
    return "subject"


def _unique_contacts(contacts: Sequence[Any]) -> list[PublishedContact]:
    seen: set[str] = set()
    out: list[PublishedContact] = []
    for item in contacts:
        if not isinstance(item, PublishedContact):
            continue
        key = item.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _seller_path(
    subject_id: str,
    partners: tuple[PartnerOpportunity, ...],
    unit: Mapping[str, Any],
) -> SellerTransactionPath:
    if partners:
        partner = partners[0]
        return SellerTransactionPath(
            path_id=partner.partner_id,
            kind=SellerPathKind(partner.direction.value),
            holder=partner.partner_name,
            vehicle=partner.vehicle_or_channel,
        )
    route = _clean(unit.get("route_role")).casefold()
    mapped = _ROUTE_TO_PATH.get(route)
    if mapped is not None:
        return SellerTransactionPath(
            path_id=f"sp:{subject_id}:{mapped.value}",
            kind=mapped,
        )
    return SellerTransactionPath(
        path_id=f"sp:{subject_id}:{SellerPathKind.PATH_UNKNOWN.value}",
        kind=SellerPathKind.PATH_UNKNOWN,
    )


def _next_action_and_tier(
    kind: AssessmentSubjectKind,
    subject: Any,
    pathway: ActionableExternalPathway,
    seller: SellerTransactionPath,
    motion: BuyingMotion,
    coverage_hold: str | None,
) -> tuple[CurrentNextAction, LeadTier]:
    holds: list[str] = []
    if seller.kind == SellerPathKind.PATH_UNKNOWN:
        holds.append(ROUTE_HOLD)
    clock_missing = (
        motion.clock is None
        and (motion.window is None
             or motion.window.label == CLOCK_UNESTABLISHED)
    )
    if clock_missing:
        holds.append(CLOCK_HOLD)
    if coverage_hold:
        holds.append(coverage_hold)
    if isinstance(subject, LiveSolicitation):
        if subject.attachment_gap:
            holds.append(
                f"attachment gap: {subject.attachment_gap}")
        if subject.classification == LiveClassification.UNSCREENED:
            holds.append(
                "live record is unscreened: finish the SAM census "
                "review before promotion")
        if (subject.recommendation == LiveRecommendation.RESEARCH
                and "attachment gap" not in " ".join(holds)):
            holds.append(
                "Assess recommendation is research: complete the "
                "named review before promotion")
    if kind == AssessmentSubjectKind.DEVELOPING_THESIS:
        if pathway.kind == PathwayKind.SAM_NOTICE:
            holds.append(
                "developing thesis cannot use a live SAM pathway")
        if getattr(subject, "status", None) == IntelligenceStatus.RESEARCH_NEEDED:
            holds.append(
                "thesis status is research_needed: "
                f"{getattr(subject, 'watch_trigger', PATHWAY_HOLD)}"
            )

    permission = CommunicationPermission.NONE
    if pathway.published_contacts:
        permission = CommunicationPermission.CONFIRM_PUBLISHED

    lead_tier = LeadTier.HOLD if holds else LeadTier.WATCH
    blocked = "; ".join(holds) if holds else GATES_NOT_READY
    verb, obj = _verb_and_object(kind, subject, pathway, holds)
    due = motion.clock
    return CurrentNextAction(
        verb=verb,
        object=obj,
        due=due,
        blocked_by=blocked,
        owner="operator",
        communication_permission=permission,
    ), lead_tier


def _verb_and_object(
    kind: AssessmentSubjectKind,
    subject: Any,
    pathway: ActionableExternalPathway,
    holds: list[str],
) -> tuple[NextActionVerb, str]:
    text = " ".join(holds).casefold()
    if "attachment" in text:
        return (
            NextActionVerb.FINISH_ATTACHMENT_INVENTORY,
            _clean(getattr(subject, "attachment_gap", None))
            or f"attachment inventory on {pathway.pathway_id}",
        )
    if "unscreened" in text or "census" in text:
        return (
            NextActionVerb.CONFIRM_SAM_CENSUS,
            f"SAM census for {getattr(subject, 'notice_id', pathway.pathway_id)}",
        )
    if "coverage" in text:
        return (
            NextActionVerb.REFRESH_STALE_COVERAGE,
            "required Assess coverage lanes",
        )
    if "clock" in text:
        return (
            NextActionVerb.WATCH_TRIGGER,
            "dated clock on the parent Assess subject",
        )
    if "seller route" in text:
        return (
            NextActionVerb.VALIDATE_PARTNER_PATH,
            getattr(subject, "next_validation_step", None)
            or f"seller path for {getattr(subject, 'record_id', pathway.pathway_id)}",
        )
    if pathway.published_contacts:
        name = pathway.published_contacts[0].name
        return (
            NextActionVerb.CONFIRM_PUBLISHED_POC,
            f"{name} on {pathway.pathway_id}",
        )
    if kind == AssessmentSubjectKind.DEVELOPING_THESIS:
        return (
            NextActionVerb.WATCH_TRIGGER,
            getattr(subject, "watch_trigger", None)
            or "thesis watch trigger",
        )
    if isinstance(subject, LiveSolicitation) and subject.requirement_excerpt:
        return (
            NextActionVerb.REVIEW_REQUIREMENT_SPAN,
            subject.requirement_excerpt,
        )
    return (
        NextActionVerb.TARGET_DOOR_STILL_LOCKED,
        GATES_NOT_READY,
    )


def _parent_trace(
    run: AssessRun, parent: OpportunityAssessment, notes: str,
) -> DecisionTrace:
    return DecisionTrace(
        trace_id=compose_trace_id(run.run_id, parent.subject_id),
        assess_run_id=run.run_id,
        as_of=run.as_of,
        parent_assessment_id=parent.assessment_id,
        assess_gate=run.approval_status,
        steps=("assess",),
        notes=notes,
    )


def _lead_trace(
    run: AssessRun,
    parent: OpportunityAssessment,
    lead: LeadRow,
    subject: Any,
) -> DecisionTrace:
    evidence_ids = tuple(
        item.evidence_id for item in lead.external_pathway.evidence)
    fit = ()
    if isinstance(subject, LiveSolicitation):
        fit = subject.fit_trace
    elif isinstance(subject, OpportunityThesis):
        fit = tuple(
            item.evidence_id for item in subject.evidence)
    targeting = lead.buying_motion.targeting_rule_id
    targeting_note = (
        f"targeting rule {targeting.value} is not a lead tier"
        if targeting is not None else "no targeting rule_id"
    )
    notes = " · ".join(
        part for part in (
            " ".join(fit),
            targeting_note,
            lead.next_action.blocked_by or "",
        ) if part
    )
    return DecisionTrace(
        trace_id=lead.decision_trace_id,
        assess_run_id=run.run_id,
        as_of=run.as_of,
        parent_assessment_id=parent.assessment_id,
        lead_id=lead.lead_id,
        assess_gate=run.approval_status,
        steps=("assess", "motion", "pathway", "path", "action", "tier"),
        evidence_ids=evidence_ids,
        notes=notes,
    )


def main(argv: list[str] | None = None) -> int:
    """Diagnostic CLI. Reads JSON; writes a draft batch to stdout."""

    parser = argparse.ArgumentParser(
        description=(
            "Map one Assess run plus an optional target_actions "
            "projection to draft LeadRows (WATCH/HOLD only)."))
    parser.add_argument(
        "--assess", required=True,
        help="Path to an AssessRun JSON object (or a golden pack "
             "wrapping one under assess_run)")
    parser.add_argument(
        "--target-actions", default=None,
        help="Optional path to a target_actions projection JSON")
    args = parser.parse_args(argv)
    assess_payload = json.loads(
        Path(args.assess).read_text(encoding="utf-8"))
    actions_payload = None
    if args.target_actions:
        actions_payload = json.loads(
            Path(args.target_actions).read_text(encoding="utf-8"))
    batch = draft_lead_rows(assess_payload, actions_payload)
    json.dump(
        batch.model_dump(mode="json"), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
