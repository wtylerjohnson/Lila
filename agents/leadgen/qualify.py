"""Real LeadRow qualifier. Fail-closed HOLD remains the default.

``draft_lead_rows`` still emits WATCH / HOLD drafts. This module is the
thin promotion path: a child may move to WATCH, LEAD_T2, or at most one
LEAD_T1 only when all four LeadRow legs and a promotion receipt are
already present. No quota fill. No invented pathway, contact, or
AssessRun.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from agents.assess.source_clock import acquired_at

from agents.assess.contracts import (
    AssessRun,
    EvidenceKind,
    EvidenceUse,
    LifecycleStage,
    OpportunityThesis,
    PartnerOpportunity,
)

from .contracts import LeadRow, OpportunityAssessment
from .enums import (
    TIER_READINESS,
    CommercialMotionKind,
    LeadTier,
    NextActionVerb,
    PathwayKind,
    SellerPathKind,
)
from .from_assess import (
    CLOCK_UNESTABLISHED,
    GATES_NOT_READY,
    AssessLeadDrafts,
)
from .next_action import CurrentNextAction
from .seller_path import SellerTransactionPath
from .traces import DecisionTrace

QUALIFIER_VERSION = "leadgen.qualify.v1"

# Incomplete required Assess coverage may leave a four-leg draft on
# WATCH. It cannot green LEAD_T1 / LEAD_T2.
_COVERAGE_HOLD_MARK = "required Assess coverage is incomplete"
_DECISION_MARKERS = (
    "renewal decision",
    "approaching decision",
    "documented decision",
    "option exercise",
    "recompete",
    "follow-on decision",
    "award decision",
)
_BARE_EXPIRY_MARKERS = (
    "period ends",
    "period of performance",
    "pop end",
    "expires",
    "expiry",
    "expiration",
)
# Role language only. Capability vocabulary is product fit, not a
# vendor component role.
_COMPONENT_ROLE_MARKERS = (
    "component role",
    "vendor component",
    "named vendor",
    "subcontractor role",
)
_RECOMPETE_MARKERS = (
    "recompete",
    "prime-led",
    "prime led",
)


class PromotionClass(str, Enum):
    """Why a draft was eligible. Not a lead tier and not targeting T1/T2."""

    INCUMBENT_RENEWAL = "incumbent_renewal"
    PRIME_RECOMPETE = "prime_recompete"
    FUNDED_PROJECT = "funded_project"


def qualify_drafts(
    batch: AssessLeadDrafts,
    run: AssessRun,
    target_actions: Mapping[str, Any] | Sequence[Any] | None = None,
    *, company_dossier=None,
) -> AssessLeadDrafts:
    """Promote a small evidenced subset. Everyone else stays fail-closed.

    Parents and lead ids are unchanged. The qualifier never mints a
    child the mapper did not already emit.
    """

    actions = target_actions if isinstance(target_actions, Mapping) else {}
    action_rows = _action_rows(actions)
    subjects = _subject_index(run)
    partners = _partners_by_link(run)
    promoted: list[LeadRow] = []
    traces_by_lead = {
        trace.lead_id: trace for trace in batch.traces if trace.lead_id
    }
    parent_traces = [trace for trace in batch.traces if not trace.lead_id]
    t1_used = False

    for lead in batch.leads:
        parent = _parent_for(batch, lead)
        subject = subjects.get(lead.buying_motion.parent_subject_id)
        linked = partners.get(lead.buying_motion.parent_subject_id, ())
        row = _joined_action_row(lead, parent, action_rows)
        next_lead, next_trace, took_t1 = _qualify_one(
            lead,
            parent,
            subject,
            linked,
            row,
            traces_by_lead.get(lead.lead_id),
            t1_used=t1_used,
        )
        if company_dossier is not None:
            if company_dossier.client_name.casefold() != run.client_name.casefold():
                raise ValueError("qualifier dossier belongs to a different client")
            text = _clean(getattr(subject, "requirement_excerpt", "")).casefold()
            import re
            words = set(re.findall(r"[a-z0-9]+", text))
            claims = (*company_dossier.offerings, *company_dossier.keywords)
            cites = tuple(sorted({eid for claim in claims
                                  if getattr(claim, "state", "") != "disputed"
                                  and (tokens := set(re.findall(r"[a-z0-9]+", _clean(
                                      getattr(claim, "text", None) or getattr(claim, "term", "")).casefold())))
                                  and len(words & tokens) >= min(2, len(tokens))
                                  for eid in claim.evidence_ids}))
            next_lead = next_lead.model_copy(update={"company_evidence_ids": cites})
            if not cites and next_lead.lead_tier in (LeadTier.LEAD_T1, LeadTier.LEAD_T2):
                next_lead, next_trace, took_t1 = _keep_hold(
                    next_lead, next_lead.seller_path, next_trace,
                    "Company dossier fit evidence is not established for this requirement")
            if next_trace is not None:
                next_trace = next_trace.model_copy(update={"notes": next_trace.notes
                    + "; company dossier considered; matched evidence: " + (", ".join(cites) or "none; fit remains open")})
        if took_t1:
            t1_used = True
        promoted.append(next_lead)
        if next_trace is not None:
            traces_by_lead[lead.lead_id] = next_trace

    return AssessLeadDrafts(
        assess_run_id=batch.assess_run_id,
        parents=batch.parents,
        leads=tuple(promoted),
        traces=tuple(parent_traces) + tuple(
            traces_by_lead[lead.lead_id]
            for lead in promoted
            if lead.lead_id in traces_by_lead
        ),
    )


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def overlays_for_promoted(
    batch: AssessLeadDrafts,
) -> dict[str, dict[str, Any]]:
    """Eval.v0 overlays keyed by lead_id / assessment_id.

    Only facts already on the LeadRow or parent. The scorer still
    refuses unresolved cites.
    """

    overlays: dict[str, dict[str, Any]] = {}
    parents = {parent.assessment_id: parent for parent in batch.parents}
    for lead in batch.leads:
        receipt = _lead_receipt(lead)
        if not receipt:
            continue
        body: dict[str, Any] = {"receipt": receipt}
        fit = [
            item.evidence_id for item in lead.external_pathway.evidence
            if item.kind is not EvidenceKind.NOTICE
            and _clean(item.evidence_id)
        ]
        if fit:
            body["product_fit_cites"] = fit
        overlays[lead.lead_id] = body
        parent = parents.get(lead.parent_assessment_id)
        if parent is None:
            continue
        parent_body = overlays.setdefault(parent.assessment_id, {})
        parent_receipts = [
            parent_body.get("receipt"),
            receipt,
        ]
        parent_body["receipt"] = " · ".join(
            part for part in parent_receipts if part)
        funding = [
            item.evidence_id for item in lead.external_pathway.evidence
            if EvidenceUse.FUNDING in item.supports and _clean(item.evidence_id)
        ]
        if funding:
            parent_body["funding_cites"] = list(dict.fromkeys(
                (*parent_body.get("funding_cites", ()), *funding)))
        contest = [
            item.evidence_id for item in lead.external_pathway.evidence
            if item.kind is not EvidenceKind.NOTICE and _clean(item.evidence_id)
        ]
        if contest:
            parent_body["contestability_cites"] = list(dict.fromkeys(
                (*parent_body.get("contestability_cites", ()), *contest)))
    return overlays


def _qualify_one(
    lead: LeadRow,
    parent: OpportunityAssessment | None,
    subject: Any,
    partners: tuple[PartnerOpportunity, ...],
    action_row: Mapping[str, Any],
    trace: DecisionTrace | None,
    *,
    t1_used: bool,
) -> tuple[LeadRow, DecisionTrace | None, bool]:
    if lead.lead_tier in (LeadTier.LEAD_T1, LeadTier.LEAD_T2) and any(
            acquired_at(e) is None for e in lead.external_pathway.evidence):
        return _source_clock_hold(lead, lead.seller_path, trace)
    if parent is None:
        return lead, trace, False
    if lead.lead_tier is LeadTier.REJECT:
        return lead, trace, False

    path = lead.seller_path
    four = _four_legs_present(lead, parent, path)
    if not four:
        return _keep_hold(lead, path, trace, "HOLD: four LeadRow legs are incomplete")

    klass = _promotion_class(lead, parent, subject, partners, action_row)
    solicitation_only = _solicitation_only(lead, path)
    blockers = _t12_blockers(lead, path)

    if solicitation_only:
        return _as_watch_or_hold(
            lead, path, trace,
            "HOLD: solicitation-only row cannot green lead T1 or lead T2",
            watch=False,
        )

    if klass is PromotionClass.FUNDED_PROJECT:
        return _as_watch_or_hold(
            lead, path, trace,
            "WATCH: funded project with defined need and timetable; "
            "T1/T2 reserved for renewal or prime-led recompete in this slice",
            watch=True,
        )

    if klass in (PromotionClass.INCUMBENT_RENEWAL, PromotionClass.PRIME_RECOMPETE):
        if any(acquired_at(e) is None for e in lead.external_pathway.evidence):
            return _source_clock_hold(lead, path, trace)
        if blockers or path.kind is SellerPathKind.PATH_UNKNOWN:
            return _as_watch_or_hold(
                lead, path, trace,
                f"WATCH: {klass.value} four-leg draft retained; "
                + (blockers or "seller route is still path_unknown"),
                watch=True,
            )
        t1_ok = (
            klass is PromotionClass.INCUMBENT_RENEWAL
            and not t1_used
            and _timing_evidenced(lead)
        )
        tier = LeadTier.LEAD_T1 if t1_ok else LeadTier.LEAD_T2
        receipt = (
            f"{tier.value}: {klass.value} with named buyer, dated clock, "
            f"published pathway, and seller route "
            f"{path.kind.value} via {path.holder or path.vehicle}"
        )
        updated, next_trace = _as_promoted(lead, path, trace, tier, receipt)
        return updated, next_trace, t1_ok

    if _coverage_or_gates_only(lead) or lead.lead_tier is LeadTier.WATCH:
        return _as_watch_or_hold(
            lead, path, trace,
            "WATCH: four-leg draft complete; approaching decision is "
            "not documented beyond expiry or a funded-project timetable",
            watch=True,
        )
    return lead, _refresh_trace(lead, path, trace, lead.next_action.blocked_by or ""), False


def _source_clock_hold(lead, path, trace):
    reason = "HOLD: source acquisition chronology is incomplete"
    previous = lead.next_action.blocked_by or ""
    if reason not in previous:
        previous = "; ".join(filter(None, (previous, reason)))
    return _as_watch_or_hold(lead, path, trace, previous, watch=False)


def _keep_hold(
    lead: LeadRow,
    path: SellerTransactionPath,
    trace: DecisionTrace | None,
    receipt: str,
) -> tuple[LeadRow, DecisionTrace | None, bool]:
    if lead.lead_tier is LeadTier.HOLD and path == lead.seller_path:
        return lead, trace, False
    updated, next_trace, took_t1 = _as_watch_or_hold(
        lead, path, trace, receipt, watch=False)
    return updated, next_trace, took_t1


def _as_watch_or_hold(
    lead: LeadRow,
    path: SellerTransactionPath,
    trace: DecisionTrace | None,
    receipt: str,
    *,
    watch: bool,
) -> tuple[LeadRow, DecisionTrace | None, bool]:
    tier = LeadTier.WATCH if watch else LeadTier.HOLD
    action = lead.next_action.model_copy(update={"blocked_by": receipt})
    updated = lead.model_copy(update={
        "seller_path": path,
        "next_action": action,
        "lead_tier": tier,
        "readiness": TIER_READINESS[tier],
        "communication_permission": action.communication_permission,
    })
    return updated, _refresh_trace(updated, path, trace, receipt), False


def _as_promoted(
    lead: LeadRow,
    path: SellerTransactionPath,
    trace: DecisionTrace | None,
    tier: LeadTier,
    receipt: str,
) -> tuple[LeadRow, DecisionTrace | None]:
    verb = (
        NextActionVerb.WATCH_TRIGGER
        if lead.buying_motion.kind is CommercialMotionKind.RENEWAL
        else NextActionVerb.VALIDATE_PARTNER_PATH
    )
    action = CurrentNextAction(
        verb=verb,
        object=receipt,
        due=lead.buying_motion.clock or lead.next_action.due,
        blocked_by=None,
        owner="operator",
        communication_permission=lead.next_action.communication_permission,
    )
    updated = lead.model_copy(update={
        "seller_path": path,
        "next_action": action,
        "lead_tier": tier,
        "readiness": TIER_READINESS[tier],
        "communication_permission": action.communication_permission,
    })
    return updated, _refresh_trace(updated, path, trace, receipt)


def _refresh_trace(
    lead: LeadRow,
    path: SellerTransactionPath,
    trace: DecisionTrace | None,
    receipt: str,
) -> DecisionTrace | None:
    if trace is None:
        return None
    note = " · ".join(
        part for part in (
            _clean(trace.notes),
            f"{QUALIFIER_VERSION} {lead.lead_tier.value}",
            receipt,
            f"path={path.kind.value}",
        ) if part
    )
    return trace.model_copy(update={"notes": note})


def _four_legs_present(
    lead: LeadRow,
    parent: OpportunityAssessment,
    path: SellerTransactionPath,
) -> bool:
    motion = lead.buying_motion
    if not _named(motion.buyer_agency):
        return False
    if motion.parent_subject_id != parent.subject_id:
        return False
    window_ok = (
        motion.clock is not None
        or (
            motion.window is not None
            and _clean(motion.window.label) not in {"", CLOCK_UNESTABLISHED}
        )
    )
    if not window_ok:
        return False
    pathway = lead.external_pathway
    if not str(pathway.source_url).lower().startswith("https://"):
        return False
    if not pathway.evidence:
        return False
    if path.kind is SellerPathKind.PATH_UNKNOWN and not _named(path.holder):
        return False
    action = lead.next_action
    if _clean(action.owner).casefold() != "operator":
        return False
    if action.verb is None:
        return False
    return True


def _promotion_class(
    lead: LeadRow,
    parent: OpportunityAssessment,
    subject: Any,
    partners: tuple[PartnerOpportunity, ...],
    action_row: Mapping[str, Any],
) -> PromotionClass | None:
    if _is_incumbent_renewal(lead, parent, subject, partners, action_row):
        return PromotionClass.INCUMBENT_RENEWAL
    if _is_prime_recompete(lead, parent, subject, partners, action_row):
        return PromotionClass.PRIME_RECOMPETE
    if _is_funded_project(lead, parent, subject):
        return PromotionClass.FUNDED_PROJECT
    return None


def _is_incumbent_renewal(
    lead: LeadRow,
    parent: OpportunityAssessment,
    subject: Any,
    partners: tuple[PartnerOpportunity, ...],
    action_row: Mapping[str, Any],
) -> bool:
    if lead.buying_motion.kind is not CommercialMotionKind.RENEWAL:
        return False
    if not _named(lead.seller_path.holder):
        return False
    return _approaching_decision(lead, subject, action_row)


def _is_prime_recompete(
    lead: LeadRow,
    parent: OpportunityAssessment,
    subject: Any,
    partners: tuple[PartnerOpportunity, ...],
    action_row: Mapping[str, Any],
) -> bool:
    del parent, subject
    prime = (
        lead.seller_path.kind is SellerPathKind.PRIME_TO_SUB
        or any(
            partner.direction.value == SellerPathKind.PRIME_TO_SUB.value
            for partner in partners
        )
    )
    if not prime:
        return False
    role_text = " ".join(part for part in (
        _clean(action_row.get("why_this_account")),
        *(partner.role_hypothesis for partner in partners),
    ) if part).casefold()
    if not any(marker in role_text for marker in _COMPONENT_ROLE_MARKERS):
        return False
    recompete_text = " ".join(part for part in (
        role_text,
        _clean(action_row.get("why_now")).casefold(),
    ) if part)
    if not any(marker in recompete_text for marker in _RECOMPETE_MARKERS):
        return False
    return True


def _is_funded_project(
    lead: LeadRow,
    parent: OpportunityAssessment,
    subject: Any,
) -> bool:
    if parent.lifecycle is not LifecycleStage.FUNDED_INTENT:
        if not isinstance(subject, OpportunityThesis):
            return False
        if subject.lifecycle_stage is not LifecycleStage.FUNDED_INTENT:
            return False
    need = _clean(parent.requirement_span)
    timetable = (
        lead.buying_motion.clock is not None
        or (
            lead.buying_motion.window is not None
            and _clean(lead.buying_motion.window.label)
            not in {"", CLOCK_UNESTABLISHED}
        )
    )
    return bool(need) and timetable


def _approaching_decision(
    lead: LeadRow,
    subject: Any,
    action_row: Mapping[str, Any],
) -> bool:
    # Requirement span is product fit, not a decision receipt.
    text = " ".join(part for part in (
        _clean(action_row.get("why_now")),
        _clean(getattr(subject, "watch_trigger", None)),
        _clean(getattr(subject, "predicted_event", None)),
        _clean(getattr(subject, "inference_chain", None)),
    ) if part).casefold()
    if not text:
        return False
    if not any(marker in text for marker in _DECISION_MARKERS):
        return False
    expiry_only = (
        any(marker in text for marker in _BARE_EXPIRY_MARKERS)
        and not any(marker in text for marker in _DECISION_MARKERS
                    if marker not in _BARE_EXPIRY_MARKERS)
    )
    if expiry_only:
        return False
    return lead.buying_motion.clock is not None or lead.next_action.due is not None


def _timing_evidenced(lead: LeadRow) -> bool:
    return any(
        item.kind is not EvidenceKind.VEHICLE
        and bool({EvidenceUse.TIMING, EvidenceUse.FUNDING} & set(item.supports))
        and _clean(item.excerpt) and _clean(item.evidence_id)
        for item in lead.external_pathway.evidence
    )


def _solicitation_only(lead: LeadRow, path: SellerTransactionPath) -> bool:
    pathway = lead.external_pathway
    has_route = any(_named(value) for value in (
        path.holder, path.vehicle, path.dossier_cite, path.prime_posture_cite))
    has_contacts = any(_named(contact.name) for contact in pathway.published_contacts)
    has_fit = any(
        item.kind is not EvidenceKind.NOTICE for item in pathway.evidence)
    notice_backed = (
        pathway.kind is PathwayKind.SAM_NOTICE
        or any(item.kind is EvidenceKind.NOTICE for item in pathway.evidence)
    )
    return notice_backed and not has_route and not has_contacts and not has_fit


def _t12_blockers(lead: LeadRow, path: SellerTransactionPath) -> str:
    parts = [part for part in (_structural_holds(lead),) if part]
    if path.kind is SellerPathKind.PATH_UNKNOWN:
        parts.append("seller route is still path_unknown")
    if _named(path.vehicle) and not _timing_evidenced(lead):
        parts.append(
            "vehicle cite without timing or funding evidence; "
            "authorization is not buying intent"
        )
    return "; ".join(parts)


def _structural_holds(lead: LeadRow) -> str:
    text = (lead.next_action.blocked_by or "").casefold()
    marks = (
        "attachment gap",
        "unscreened",
        "live record is unscreened",
        "assess recommendation is research",
        "developing thesis cannot use a live sam pathway",
        "clock unestablished",
        "seller route unestablished",
        "pathway unestablished",
        _COVERAGE_HOLD_MARK.casefold(),
    )
    hits = [mark for mark in marks if mark in text]
    return "; ".join(hits)


def _coverage_or_gates_only(lead: LeadRow) -> bool:
    text = (lead.next_action.blocked_by or "").strip()
    if not text:
        return lead.lead_tier is LeadTier.WATCH
    if _structural_holds(lead):
        return False
    return (
        _COVERAGE_HOLD_MARK.casefold() in text.casefold()
        or GATES_NOT_READY.casefold() in text.casefold()
    )


def _lead_receipt(lead: LeadRow) -> str:
    return _clean(lead.next_action.blocked_by) or _clean(lead.next_action.object)


def _named(value: str | None) -> bool:
    token = "".join(
        char for char in str(value or "").casefold() if char.isalnum())
    return token not in {
        "", "unknown", "unk", "na", "none", "tbd", "x", "placeholder",
        "tba", "null", "tobedetermined", "tobeannounced",
        "notavailable", "notapplicable", "notknown",
    }


def _parent_for(
    batch: AssessLeadDrafts, lead: LeadRow,
) -> OpportunityAssessment | None:
    for parent in batch.parents:
        if parent.assessment_id == lead.parent_assessment_id:
            return parent
    return None


def _subject_index(run: AssessRun) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for record in run.live.records:
        items[record.record_id] = record
    for thesis in run.horizon.items:
        items[thesis.thesis_id] = thesis
    for partner in run.partners.items:
        items[partner.partner_id] = partner
    return items


def _partners_by_link(
    run: AssessRun,
) -> dict[str, tuple[PartnerOpportunity, ...]]:
    grouped: dict[str, list[PartnerOpportunity]] = {}
    for partner in run.partners.items:
        for link in partner.linked_assess_ids:
            grouped.setdefault(link, []).append(partner)
    return {key: tuple(values) for key, values in grouped.items()}


def _action_rows(projection: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in list(projection.get("rows") or []):
        if isinstance(row, Mapping):
            rows.append(dict(row))
    for group in projection.get("groups") or []:
        if isinstance(group, Mapping):
            for row in group.get("rows") or []:
                if isinstance(row, Mapping):
                    rows.append(dict(row))
    return rows


def _joined_action_row(
    lead: LeadRow,
    parent: OpportunityAssessment | None,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    keys = {
        _clean(lead.buying_motion.motion_id).casefold(),
        _clean(lead.buying_motion.parent_subject_id).casefold(),
    }
    if parent is not None:
        keys.update({
            _clean(parent.subject_id).casefold(),
            _clean(parent.notice_id).casefold(),
            _clean(parent.solicitation_number).casefold(),
        })
    keys.discard("")
    for row in rows:
        row_keys = {
            _clean(row.get("motion_id")).casefold(),
            _clean(row.get("notice_id")).casefold(),
            _clean(row.get("solicitation_number")).casefold(),
        }
        for record_id in row.get("record_ids") or []:
            row_keys.add(_clean(record_id).casefold())
        row_keys.discard("")
        if keys & row_keys:
            return row
    return {}
