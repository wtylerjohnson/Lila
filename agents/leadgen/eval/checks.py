"""Deterministic A-F / parent / pack checks. Zero LLM, zero network."""

from __future__ import annotations

from collections.abc import Iterable

from agents.leadgen.contracts import LeadRow, OpportunityAssessment
from agents.leadgen.enums import (
    CommercialMotionKind,
    CommunicationPermission,
    LeadTier,
    PathwayKind,
    SellerPathKind,
)
from agents.leadgen.traces import DecisionTrace
from agents.assess.contracts import LifecycleStage

from .load import overlay_for, overlay_index
from .models import (
    CheckResult,
    CheckVerdict,
    EmailStatus,
    EvalOverlay,
    ScoreInput,
    Scorecard,
)

_UNKNOWN_BUYERS = frozenset({"", "unknown", "unk", "n/a", "na", "none"})
_INTENT_MOTIONS = frozenset({
    CommercialMotionKind.LIVE_BID,
    CommercialMotionKind.RENEWAL,
    CommercialMotionKind.DISPLACEMENT,
})
_HELD_TIERS = frozenset({LeadTier.WATCH, LeadTier.HOLD, LeadTier.REJECT})
_ACTIONABLE_TIERS = frozenset({LeadTier.LEAD_T1, LeadTier.LEAD_T2})


def result(
    check_id: str,
    family: str,
    title: str,
    verdict: CheckVerdict,
    receipt: str,
    *,
    scope: str,
    subject_id: str,
    lead_tier: str | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        family=family,
        title=title,
        verdict=verdict,
        receipt=_one_line(receipt),
        scope=scope,  # type: ignore[arg-type]
        subject_id=subject_id,
        lead_tier=lead_tier,
    )


def family_pass(checks: Iterable[CheckResult], family: str) -> bool:
    """Family PASS when no member FAILs. NA does not sink the family."""

    return not any(
        item.family == family and item.verdict is CheckVerdict.FAIL
        for item in checks
    )


def is_solicitation_only(lead: LeadRow, overlay: EvalOverlay) -> bool:
    """True when a child is a wrapped SAM notice, not a four-factor lead."""

    if overlay.solicitation_only is True:
        return True
    if overlay.solicitation_only is False:
        return False
    path = lead.seller_path
    has_route_cite = bool(
        path.holder or path.vehicle or path.dossier_cite
        or path.prime_posture_cite)
    has_contacts = bool(lead.external_pathway.published_contacts)
    has_fit = bool(overlay.product_fit_cites)
    return (
        lead.external_pathway.kind is PathwayKind.SAM_NOTICE
        and not has_route_cite
        and not has_contacts
        and not has_fit
    )


def score_input(pack: ScoreInput) -> Scorecard:
    """Score every parent and every child, including REJECT."""

    overlays = overlay_index(pack.overlays)
    parents = {parent.assessment_id: parent for parent in pack.parents}
    traces = {trace.trace_id: trace for trace in pack.traces}
    checks: list[CheckResult] = []
    for parent in pack.parents:
        overlay = overlay_for(overlays, parent.assessment_id)
        checks.extend(score_parent(parent, pack.leads, overlay))
    for lead in pack.leads:
        overlay = overlay_for(overlays, lead.lead_id)
        parent = parents.get(lead.parent_assessment_id)
        trace = traces.get(lead.decision_trace_id)
        checks.extend(score_lead(lead, parent, trace, overlay))
    checks.extend(score_pack(pack, checks))
    reject_ids = tuple(
        lead.lead_id for lead in pack.leads
        if lead.lead_tier is LeadTier.REJECT
    )
    return Scorecard(
        source_label=pack.source_label,
        checks=tuple(checks),
        lead_ids=tuple(lead.lead_id for lead in pack.leads),
        reject_lead_ids=reject_ids,
        invented_row_count=0,
    )


def score_lead(
    lead: LeadRow,
    parent: OpportunityAssessment | None,
    trace: DecisionTrace | None,
    overlay: EvalOverlay,
) -> list[CheckResult]:
    """A-F plus per-row tier checks. REJECT is scored, never dropped."""

    sid = lead.lead_id
    tier = lead.lead_tier.value
    rows = [
        _a1(lead), _a2(lead), _a3(lead), _a4(lead, parent),
        _b1(lead, parent, overlay), _b2(lead, parent, overlay),
        _b3(lead, parent),
        _c1(lead), _c2(lead, overlay), _c3(lead, overlay),
        _d1(lead), _d2(lead), _d3(lead, overlay),
        _e1(lead), _e2(lead), _e3(lead),
        _f1(lead, trace), _f2(lead, trace), _f3(lead, parent),
    ]
    af_pass = all(family_pass(rows, family) for family in "ABCDEF")
    if lead.lead_tier in _ACTIONABLE_TIERS:
        if af_pass:
            rows.append(result(
                "TIER.T1T2_REQUIRES_AF", "TIER",
                "lead T1/T2 require A-F PASS",
                CheckVerdict.PASS,
                "families A-F all PASS",
                scope="lead", subject_id=sid, lead_tier=tier,
            ))
        else:
            failed = [
                item.check_id for item in rows
                if item.verdict is CheckVerdict.FAIL
                and item.family in set("ABCDEF")
            ]
            rows.append(result(
                "TIER.T1T2_REQUIRES_AF", "TIER",
                "lead T1/T2 require A-F PASS",
                CheckVerdict.FAIL,
                "A-F FAIL on " + ", ".join(failed) if failed else
                "A-F did not all PASS",
                scope="lead", subject_id=sid, lead_tier=tier,
            ))
    else:
        rows.append(result(
            "TIER.T1T2_REQUIRES_AF", "TIER",
            "lead T1/T2 require A-F PASS",
            CheckVerdict.NA,
            f"{tier} is not required to clear A-F",
            scope="lead", subject_id=sid, lead_tier=tier,
        ))
    if lead.lead_tier in _ACTIONABLE_TIERS and is_solicitation_only(
            lead, overlay):
        rows.append(result(
            "TIER.SOLICITATION_ONLY", "TIER",
            "solicitation is not a lead",
            CheckVerdict.FAIL,
            "T1/T2 is a wrapped SAM notice; solicitation-only cannot "
            "be lead T1 or lead T2",
            scope="lead", subject_id=sid, lead_tier=tier,
        ))
    elif lead.lead_tier in _ACTIONABLE_TIERS:
        rows.append(result(
            "TIER.SOLICITATION_ONLY", "TIER",
            "solicitation is not a lead",
            CheckVerdict.PASS,
            "row cites a route, contact, or fit beyond the notice",
            scope="lead", subject_id=sid, lead_tier=tier,
        ))
    else:
        rows.append(result(
            "TIER.SOLICITATION_ONLY", "TIER",
            "solicitation is not a lead",
            CheckVerdict.NA,
            f"{tier} may watch a notice; it is not a T1/T2 claim",
            scope="lead", subject_id=sid, lead_tier=tier,
        ))
        rows.append(_receipt_justified(lead, overlay, trace))
    return rows


def score_parent(
    parent: OpportunityAssessment,
    leads: tuple[LeadRow, ...],
    overlay: EvalOverlay,
) -> list[CheckResult]:
    children = tuple(
        lead for lead in leads
        if lead.parent_assessment_id == parent.assessment_id
    )
    sid = parent.assessment_id
    return [
        _p_demand(parent, overlay, sid),
        _p_buyer(parent, sid),
        _p_need(parent, overlay, sid),
        _p_funding(parent, overlay, sid),
        _p_timing(parent, sid),
        _p_fit(parent, overlay, sid),
        _p_mechanism(parent, sid),
        _p_contestability(parent, overlay, sid),
        _p_blockers(parent, children, overlay, sid),
        _p_evidence(parent, children, overlay, sid),
    ]


def score_pack(
    pack: ScoreInput,
    already: list[CheckResult],
) -> list[CheckResult]:
    overlays = overlay_index(pack.overlays)
    solicitation_t1t2 = [
        lead.lead_id for lead in pack.leads
        if lead.lead_tier in _ACTIONABLE_TIERS
        and is_solicitation_only(lead, overlay_for(overlays, lead.lead_id))
    ]
    reject_ids = [
        lead.lead_id for lead in pack.leads
        if lead.lead_tier is LeadTier.REJECT
    ]
    scored_rejects = {
        item.subject_id for item in already
        if item.scope == "lead" and item.lead_tier == LeadTier.REJECT.value
    }
    rows = [
        result(
            "PACK.SOLICITATION_ONLY_T1T2", "PACK",
            "zero solicitation-only T1/T2",
            CheckVerdict.FAIL if solicitation_t1t2 else CheckVerdict.PASS,
            (
                "solicitation-only T1/T2: " + ", ".join(solicitation_t1t2)
                if solicitation_t1t2 else
                "0 solicitation-only lead T1 / lead T2"
            ),
            scope="pack", subject_id=pack.source_label,
        ),
        result(
            "PACK.NO_QUOTA_FILL", "PACK",
            "no quota fill",
            CheckVerdict.FAIL if pack.quota_keys else CheckVerdict.PASS,
            (
                "quota keys are not allowed: " + ", ".join(pack.quota_keys)
                if pack.quota_keys else
                "scorer invented 0 rows; pack declares no fill quota"
            ),
            scope="pack", subject_id=pack.source_label,
        ),
        result(
            "PACK.REJECT_NOT_DROPPED", "PACK",
            "REJECT receipt without silent drop",
            (
                CheckVerdict.FAIL
                if any(lead_id not in scored_rejects for lead_id in reject_ids)
                else CheckVerdict.PASS
            ),
            (
                f"REJECT rows on scorecard: {len(scored_rejects)} of "
                f"{len(reject_ids)} input"
            ),
            scope="pack", subject_id=pack.source_label,
        ),
    ]
    return rows


def _named(value: str | None) -> bool:
    return " ".join(str(value or "").split()).casefold() not in _UNKNOWN_BUYERS


def _one_line(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned or "no receipt"


def _a1(lead: LeadRow) -> CheckResult:
    buyer = lead.buying_motion.buyer_agency
    ok = _named(buyer)
    return result(
        "A1", "A", "buyer named",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        f"buyer_agency={buyer}" if ok else "buying motion has no named buyer",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _a2(lead: LeadRow) -> CheckResult:
    kind = lead.buying_motion.kind
    ok = isinstance(kind, CommercialMotionKind)
    return result(
        "A2", "A", "closed commercial motion",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        f"kind={kind.value}",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _a3(lead: LeadRow) -> CheckResult:
    motion = lead.buying_motion
    ok = motion.clock is not None or motion.window is not None
    return result(
        "A3", "A", "clock or projected window",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        (
            f"clock={motion.clock}" if motion.clock is not None else
            f"window={motion.window.label}" if motion.window is not None else
            "neither clock nor window"
        ),
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _a4(lead: LeadRow, parent: OpportunityAssessment | None) -> CheckResult:
    bound = lead.buying_motion.parent_subject_id
    if parent is None:
        return result(
            "A4", "A", "motion bound to parent subject",
            CheckVerdict.FAIL,
            f"parent missing; motion parent_subject_id={bound}",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    ok = bound == parent.subject_id
    return result(
        "A4", "A", "motion bound to parent subject",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        (
            f"parent_subject_id={bound} matches {parent.subject_id}"
            if ok else
            f"parent_subject_id={bound} != parent.subject_id="
            f"{parent.subject_id}"
        ),
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _b1(
    lead: LeadRow,
    parent: OpportunityAssessment | None,
    overlay: EvalOverlay,
) -> CheckResult:
    span = (parent.requirement_span if parent is not None else None) or ""
    cites = overlay.product_fit_cites
    ok = bool(span.strip()) or bool(cites)
    return result(
        "B1", "B", "capability-fit evidence",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        (
            f"requirement_span present ({len(span.split())} words)"
            if span.strip() else
            "product_fit_cites=" + ", ".join(cites) if cites else
            "no requirement_span and no product_fit_cites"
        ),
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _b2(
    lead: LeadRow,
    parent: OpportunityAssessment | None,
    overlay: EvalOverlay,
) -> CheckResult:
    if overlay.product_fit_cites:
        return result(
            "B2", "B", "fit is not title/NAICS-only",
            CheckVerdict.PASS,
            "overlay product_fit_cites are distinct from the title",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    span = " ".join(
        ((parent.requirement_span if parent is not None else None) or "")
        .split())
    title = " ".join((parent.title if parent is not None else "") .split())
    if not span:
        return result(
            "B2", "B", "fit is not title/NAICS-only",
            CheckVerdict.FAIL,
            "no fit cite beyond a missing requirement_span",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if title and span.casefold() == title.casefold():
        return result(
            "B2", "B", "fit is not title/NAICS-only",
            CheckVerdict.FAIL,
            "requirement_span repeats the parent title",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "B2", "B", "fit is not title/NAICS-only",
        CheckVerdict.PASS,
        "requirement_span is distinct from the parent title",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _b3(lead: LeadRow, parent: OpportunityAssessment | None) -> CheckResult:
    status = (parent.identity_status if parent is not None else None) or ""
    cleaned = status.strip().casefold()
    if cleaned in {"blocked", "abstain"}:
        return result(
            "B3", "B", "seller identity not blocked",
            CheckVerdict.FAIL,
            f"parent identity_status={status}",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if cleaned in {"", "bound"}:
        return result(
            "B3", "B", "seller identity not blocked",
            CheckVerdict.PASS if cleaned == "bound" else CheckVerdict.NA,
            (
                "identity_status=bound" if cleaned == "bound" else
                "no identity_status on parent; Step 1 not required here"
            ),
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "B3", "B", "seller identity not blocked",
        CheckVerdict.PASS,
        f"identity_status={status}",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _c1(lead: LeadRow) -> CheckResult:
    pathway = lead.external_pathway
    url = str(pathway.source_url or "")
    ok = url.lower().startswith("https://") and bool(pathway.evidence)
    return result(
        "C1", "C", "published evidence-bound pathway",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        (
            f"kind={pathway.kind.value} evidence={len(pathway.evidence)}"
            if ok else "pathway missing HTTPS URL or evidence"
        ),
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _c2(lead: LeadRow, overlay: EvalOverlay) -> CheckResult:
    perm = lead.communication_permission
    if perm is CommunicationPermission.OUTREACH_AUTHORIZED:
        if overlay.outreach_doors_open:
            return result(
                "C2", "C", "communication permission is closed",
                CheckVerdict.PASS,
                "outreach_authorized with overlay doors open",
                scope="lead", subject_id=lead.lead_id,
                lead_tier=lead.lead_tier.value,
            )
        return result(
            "C2", "C", "communication permission is closed",
            CheckVerdict.FAIL,
            "outreach_authorized without overlay outreach_doors_open",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "C2", "C", "communication permission is closed",
        CheckVerdict.PASS,
        f"communication_permission={perm.value}",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _c3(lead: LeadRow, overlay: EvalOverlay) -> CheckResult:
    """UNVERIFIED email cannot PASS C3. Absent email is NA, not a hide."""

    status = overlay.email_status
    if status is EmailStatus.UNVERIFIED:
        addr = overlay.email or "unspecified"
        return result(
            "C3", "C", "contact email is not UNVERIFIED",
            CheckVerdict.FAIL,
            f"UNVERIFIED email cannot PASS C3 ({addr})",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if status is EmailStatus.VERIFIED:
        return result(
            "C3", "C", "contact email is not UNVERIFIED",
            CheckVerdict.PASS,
            "contact email marked VERIFIED",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "C3", "C", "contact email is not UNVERIFIED",
        CheckVerdict.NA,
        "no email claimed; C3 does not invent a contact",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _d1(lead: LeadRow) -> CheckResult:
    kind = lead.seller_path.kind
    if kind is not SellerPathKind.PATH_UNKNOWN:
        return result(
            "D1", "D", "seller path established or held",
            CheckVerdict.PASS,
            f"kind={kind.value}",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    hold = (lead.next_action.blocked_by or "").strip()
    if lead.lead_tier in _HELD_TIERS and hold:
        return result(
            "D1", "D", "seller path established or held",
            CheckVerdict.PASS,
            f"path_unknown held with receipt: {hold}",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "D1", "D", "seller path established or held",
        CheckVerdict.FAIL,
        "path_unknown with no hold receipt",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _d2(lead: LeadRow) -> CheckResult:
    path = lead.seller_path
    cite = path.holder or path.vehicle or path.dossier_cite or path.prime_posture_cite
    if cite:
        return result(
            "D2", "D", "path cites holder/vehicle/dossier",
            CheckVerdict.PASS,
            f"path cite present ({cite})",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if path.kind is SellerPathKind.PATH_UNKNOWN and lead.lead_tier in _HELD_TIERS:
        return result(
            "D2", "D", "path cites holder/vehicle/dossier",
            CheckVerdict.NA,
            "unknown path has no cite to judge; see D1",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "D2", "D", "path cites holder/vehicle/dossier",
        CheckVerdict.FAIL,
        "seller path has no holder, vehicle, dossier, or posture cite",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _d3(lead: LeadRow, overlay: EvalOverlay) -> CheckResult:
    """Authorization is not buying intent. Auth-only cannot PASS D3."""

    motion = lead.buying_motion
    dated_intent = (
        motion.kind in _INTENT_MOTIONS
        and (motion.clock is not None or motion.window is not None)
    )
    vehicle_like = (
        lead.seller_path.kind is SellerPathKind.VEHICLE_ACCESS
        or bool(lead.seller_path.vehicle)
    )
    if overlay.auth_only:
        return result(
            "D3", "D", "authorization is not buying intent",
            CheckVerdict.FAIL,
            "auth-only cannot PASS D3; vehicle/authorization is not intent",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if vehicle_like and not dated_intent:
        return result(
            "D3", "D", "authorization is not buying intent",
            CheckVerdict.FAIL,
            "auth-only cannot PASS D3; vehicle path has no dated buying intent",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if dated_intent:
        return result(
            "D3", "D", "authorization is not buying intent",
            CheckVerdict.PASS,
            f"dated {motion.kind.value} intent; path is not auth-only",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "D3", "D", "authorization is not buying intent",
        CheckVerdict.FAIL,
        "no dated buying intent; authorization cannot stand in for demand",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _e1(lead: LeadRow) -> CheckResult:
    verb = lead.next_action.verb
    return result(
        "E1", "E", "closed next-action verb",
        CheckVerdict.PASS,
        f"verb={verb.value}",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _e2(lead: LeadRow) -> CheckResult:
    due = lead.next_action.due
    clock = lead.buying_motion.clock
    hold = (lead.next_action.blocked_by or "").strip()
    if due is not None:
        return result(
            "E2", "E", "action clock or named missing clock",
            CheckVerdict.PASS,
            f"due={due}",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if clock is not None and not hold:
        return result(
            "E2", "E", "action clock or named missing clock",
            CheckVerdict.FAIL,
            "motion has a clock but next_action.due is empty and "
            "blocked_by is empty",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "E2", "E", "action clock or named missing clock",
        CheckVerdict.PASS,
        hold or "no motion clock; undated action is honest",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _e3(lead: LeadRow) -> CheckResult:
    owner = (lead.next_action.owner or "").strip()
    ok = owner.casefold() == "operator"
    return result(
        "E3", "E", "owner is the operator",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        f"owner={owner}" if ok else f"invented-person owner={owner}",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _f1(lead: LeadRow, trace: DecisionTrace | None) -> CheckResult:
    if trace is None:
        return result(
            "F1", "F", "decision trace present",
            CheckVerdict.FAIL,
            f"decision_trace_id={lead.decision_trace_id} is not in the pack",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "F1", "F", "decision trace present",
        CheckVerdict.PASS,
        f"trace_id={trace.trace_id}",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _f2(lead: LeadRow, trace: DecisionTrace | None) -> CheckResult:
    if trace is None:
        return result(
            "F2", "F", "trace evidence or honest gap",
            CheckVerdict.FAIL,
            "no trace to audit",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if trace.evidence_ids:
        return result(
            "F2", "F", "trace evidence or honest gap",
            CheckVerdict.PASS,
            "evidence_ids=" + ", ".join(trace.evidence_ids),
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    if (trace.notes or "").strip():
        return result(
            "F2", "F", "trace evidence or honest gap",
            CheckVerdict.PASS,
            "empty evidence_ids with gap note: " + _one_line(trace.notes),
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "F2", "F", "trace evidence or honest gap",
        CheckVerdict.FAIL,
        "trace has no evidence_ids and no gap note",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _f3(lead: LeadRow, parent: OpportunityAssessment | None) -> CheckResult:
    if parent is None:
        return result(
            "F3", "F", "parent assessment resolves",
            CheckVerdict.FAIL,
            f"parent_assessment_id={lead.parent_assessment_id} missing",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "F3", "F", "parent assessment resolves",
        CheckVerdict.PASS,
        f"parent={parent.assessment_id}",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _receipt_justified(
    lead: LeadRow,
    overlay: EvalOverlay,
    trace: DecisionTrace | None,
) -> CheckResult:
    text = _receipt_text(lead, overlay, trace)
    if lead.lead_tier in _HELD_TIERS:
        if text:
            return result(
                "TIER.RECEIPT_JUSTIFIED", "TIER",
                "WATCH/HOLD/REJECT justified by receipt",
                CheckVerdict.PASS,
                text,
                scope="lead", subject_id=lead.lead_id,
                lead_tier=lead.lead_tier.value,
            )
        return result(
            "TIER.RECEIPT_JUSTIFIED", "TIER",
            "WATCH/HOLD/REJECT justified by receipt",
            CheckVerdict.FAIL,
            f"{lead.lead_tier.value} has no blocked_by, overlay receipt, "
            "or trace notes",
            scope="lead", subject_id=lead.lead_id,
            lead_tier=lead.lead_tier.value,
        )
    return result(
        "TIER.RECEIPT_JUSTIFIED", "TIER",
        "WATCH/HOLD/REJECT justified by receipt",
        CheckVerdict.NA,
        "actionable tier uses A-F instead of a hold receipt",
        scope="lead", subject_id=lead.lead_id,
        lead_tier=lead.lead_tier.value,
    )


def _receipt_text(
    lead: LeadRow,
    overlay: EvalOverlay,
    trace: DecisionTrace | None,
) -> str:
    parts = [
        (lead.next_action.blocked_by or "").strip(),
        (overlay.receipt or "").strip(),
        (trace.notes if trace is not None else "").strip(),
    ]
    return " · ".join(part for part in parts if part)


def _p_demand(
    parent: OpportunityAssessment, overlay: EvalOverlay, sid: str,
) -> CheckResult:
    if overlay.demand_cites or parent.live_classification or parent.lifecycle:
        return result(
            "P.DEMAND", "P", "demand reality",
            CheckVerdict.PASS,
            (
                "demand_cites=" + ", ".join(overlay.demand_cites)
                if overlay.demand_cites else
                f"classification={getattr(parent.live_classification, 'value', None)} "
                f"lifecycle={getattr(parent.lifecycle, 'value', None)}"
            ),
            scope="parent", subject_id=sid,
        )
    return result(
        "P.DEMAND", "P", "demand reality",
        CheckVerdict.FAIL,
        "no live classification, lifecycle, or demand cite",
        scope="parent", subject_id=sid,
    )


def _p_buyer(parent: OpportunityAssessment, sid: str) -> CheckResult:
    ok = _named(parent.agency)
    return result(
        "P.BUYER", "P", "buyer identity",
        CheckVerdict.PASS if ok else CheckVerdict.FAIL,
        f"agency={parent.agency}" if ok else "parent agency is unnamed",
        scope="parent", subject_id=sid,
    )


def _p_need(
    parent: OpportunityAssessment, overlay: EvalOverlay, sid: str,
) -> CheckResult:
    span = (parent.requirement_span or "").strip()
    if span or overlay.need_cites:
        return result(
            "P.NEED", "P", "need",
            CheckVerdict.PASS,
            "requirement_span present" if span else
            "need_cites=" + ", ".join(overlay.need_cites),
            scope="parent", subject_id=sid,
        )
    return result(
        "P.NEED", "P", "need",
        CheckVerdict.FAIL,
        "no requirement_span and no need_cites",
        scope="parent", subject_id=sid,
    )


def _p_funding(
    parent: OpportunityAssessment, overlay: EvalOverlay, sid: str,
) -> CheckResult:
    if overlay.funding_cites:
        return result(
            "P.FUNDING", "P", "funding",
            CheckVerdict.PASS,
            "funding_cites=" + ", ".join(overlay.funding_cites),
            scope="parent", subject_id=sid,
        )
    if parent.lifecycle is LifecycleStage.FUNDED_INTENT:
        return result(
            "P.FUNDING", "P", "funding",
            CheckVerdict.PASS,
            "lifecycle=funded_intent",
            scope="parent", subject_id=sid,
        )
    return result(
        "P.FUNDING", "P", "funding",
        CheckVerdict.FAIL,
        "a solicitation is not proof of funding",
        scope="parent", subject_id=sid,
    )


def _p_timing(parent: OpportunityAssessment, sid: str) -> CheckResult:
    if parent.lifecycle is not None:
        return result(
            "P.TIMING", "P", "timing",
            CheckVerdict.PASS,
            f"lifecycle={parent.lifecycle.value}",
            scope="parent", subject_id=sid,
        )
    return result(
        "P.TIMING", "P", "timing",
        CheckVerdict.FAIL,
        "parent has no lifecycle",
        scope="parent", subject_id=sid,
    )


def _p_fit(
    parent: OpportunityAssessment, overlay: EvalOverlay, sid: str,
) -> CheckResult:
    span = (parent.requirement_span or "").strip()
    if span or overlay.product_fit_cites:
        return result(
            "P.FIT", "P", "fit",
            CheckVerdict.PASS,
            "requirement_span present" if span else
            "product_fit_cites=" + ", ".join(overlay.product_fit_cites),
            scope="parent", subject_id=sid,
        )
    return result(
        "P.FIT", "P", "fit",
        CheckVerdict.FAIL,
        "no requirement_span and no product_fit_cites",
        scope="parent", subject_id=sid,
    )


def _p_mechanism(parent: OpportunityAssessment, sid: str) -> CheckResult:
    if parent.notice_id or parent.solicitation_number:
        return result(
            "P.MECHANISM", "P", "mechanism",
            CheckVerdict.PASS,
            f"notice_id={parent.notice_id} solicitation_number="
            f"{parent.solicitation_number}",
            scope="parent", subject_id=sid,
        )
    return result(
        "P.MECHANISM", "P", "mechanism",
        CheckVerdict.FAIL,
        "no notice_id or solicitation_number",
        scope="parent", subject_id=sid,
    )


def _p_contestability(
    parent: OpportunityAssessment, overlay: EvalOverlay, sid: str,
) -> CheckResult:
    if overlay.contestability_cites:
        return result(
            "P.CONTESTABILITY", "P", "contestability",
            CheckVerdict.PASS,
            "contestability_cites=" + ", ".join(overlay.contestability_cites),
            scope="parent", subject_id=sid,
        )
    return result(
        "P.CONTESTABILITY", "P", "contestability",
        CheckVerdict.FAIL,
        "a notice is not proof the buy is contestable",
        scope="parent", subject_id=sid,
    )


def _p_blockers(
    parent: OpportunityAssessment,
    children: tuple[LeadRow, ...],
    overlay: EvalOverlay,
    sid: str,
) -> CheckResult:
    held = [lead for lead in children if lead.lead_tier in _HELD_TIERS]
    if overlay.blocker_cites:
        return result(
            "P.BLOCKERS", "P", "blockers",
            CheckVerdict.PASS,
            "blocker_cites=" + ", ".join(overlay.blocker_cites),
            scope="parent", subject_id=sid,
        )
    named = [
        (lead.next_action.blocked_by or "").strip()
        for lead in held if (lead.next_action.blocked_by or "").strip()
    ]
    if held and not named:
        return result(
            "P.BLOCKERS", "P", "blockers",
            CheckVerdict.FAIL,
            "HOLD/REJECT children with no named blocker",
            scope="parent", subject_id=sid,
        )
    if named:
        return result(
            "P.BLOCKERS", "P", "blockers",
            CheckVerdict.PASS,
            "child blocked_by: " + " · ".join(named),
            scope="parent", subject_id=sid,
        )
    return result(
        "P.BLOCKERS", "P", "blockers",
        CheckVerdict.PASS,
        "no HOLD/REJECT children; no blocker receipt required",
        scope="parent", subject_id=sid,
    )


def _p_evidence(
    parent: OpportunityAssessment,
    children: tuple[LeadRow, ...],
    overlay: EvalOverlay,
    sid: str,
) -> CheckResult:
    child_evidence = [
        item.evidence_id
        for lead in children
        for item in lead.external_pathway.evidence
    ]
    if parent.notice_id or child_evidence or overlay.demand_cites:
        return result(
            "P.EVIDENCE", "P", "evidence family",
            CheckVerdict.PASS,
            (
                f"notice_id={parent.notice_id}"
                if parent.notice_id else
                "child evidence=" + ", ".join(child_evidence[:4])
                if child_evidence else
                "demand_cites=" + ", ".join(overlay.demand_cites)
            ),
            scope="parent", subject_id=sid,
        )
    return result(
        "P.EVIDENCE", "P", "evidence family",
        CheckVerdict.FAIL,
        "parent has no notice_id, child evidence, or overlay cite",
        scope="parent", subject_id=sid,
    )

