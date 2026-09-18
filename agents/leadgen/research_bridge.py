"""Map an evidenced research buying event into the existing four-leg lead path."""
from agents.assess.reviewed_cases import ReviewedSubject
from agents.assess.contracts import LifecycleStage
from .contracts import LeadRow
from .enums import (CommercialMotionKind, CommunicationPermission, Contactability,
                    LeadTier, LeadReadiness, NextActionVerb, PathwayKind)
from .ids import compose_lead_id, compose_trace_id
from .motion import BuyingMotion
from .next_action import CurrentNextAction
from .pathway import ActionableExternalPathway, PublishedContact
from .seller_path import SellerTransactionPath
from .traces import DecisionTrace
from .research_event import event_gaps, compact


def overlay_for(subject):
    return (ReviewedSubject.model_validate_json(subject.reviewed_overlay_json)
            if subject.reviewed_overlay_json else None)


def research_draft(run, parent, subject, coverage_hold):
    overlay = overlay_for(subject)
    event = overlay.buying_event if overlay else None
    if event is None:
        return None, "Current buying decision, requirement owner, eligible supplier and verified email/mobile remain to be established"
    refs = {e.evidence_id: e for e in overlay.research.evidence}
    claims = {c.kind: c for c in event.claims}
    # A proposal without even a source pathway remains a parent, not a dummy child.
    owner = refs.get(claims['owner'].evidence_id)
    target = next((t for t in overlay.targets if compact(t.name) == compact(event.target_name)), None)
    if owner is None or target is None:
        return None, "Buying-event proposal lacks a source-bound responsible target"
    gaps = event_gaps(event, refs.values(), overlay.targets, as_of=run.as_of, client_name=run.client_name)
    if coverage_hold:
        gaps.append(coverage_hold)
    motion = BuyingMotion(motion_id=event.event_id, parent_subject_id=subject.subject_id,
        kind=CommercialMotionKind.ADJACENCY, stage=LifecycleStage.ACQUISITION_PLANNING,
        buyer_agency=subject.agency, buyer_component=subject.component,
        buyer_office=event.buyer_office, clock=event.decision_date, recorded_at=run.as_of)
    pathway = ActionableExternalPathway(pathway_id='research-poc:' + event.event_id,
        kind=PathwayKind.PUBLISHED_POC, source_url=owner.source_url, evidence=tuple(refs.values()),
        published_contacts=(PublishedContact(name=target.name, title=target.role, source_url=owner.source_url),),
        contactability=Contactability.PUBLISHED_POC)
    path = SellerTransactionPath(path_id='research-supplier:' + event.event_id,
        kind=event.seller_path, holder=event.supplier, dossier_cite=claims['seller_route'].evidence_id)
    lead_id = compose_lead_id(parent.assessment_id, motion.motion_id, pathway.pathway_id, path.path_id)
    trace_id = compose_trace_id(run.run_id, lead_id)
    reason = '; '.join(gaps) or 'Buying-event evidence awaits deterministic qualification'
    lead = LeadRow(lead_id=lead_id, parent_assessment_id=parent.assessment_id, assess_run_id=run.run_id,
        buying_motion=motion, external_pathway=pathway, seller_path=path,
        next_action=CurrentNextAction(verb=NextActionVerb.CONFIRM_PUBLISHED_POC,
            object=target.next_ask, due=event.decision_date, blocked_by=reason),
        lead_tier=LeadTier.HOLD, readiness=LeadReadiness.HELD,
        contactability=Contactability.PUBLISHED_POC, communication_permission=CommunicationPermission.NONE,
        decision_trace_id=trace_id, targets=(target,), research=overlay.research)
    trace = DecisionTrace(trace_id=trace_id, assess_run_id=run.run_id, as_of=run.as_of,
        parent_assessment_id=parent.assessment_id, lead_id=lead_id,
        steps=('research_buying_event',), evidence_ids=tuple(refs), notes=reason)
    return (lead, trace), None


def qualify_research(lead, parent, run, trace):
    from .from_assess import _coverage_hold
    from .qualify import _as_watch_or_hold, _refresh_trace
    from .enums import TIER_READINESS
    overlay = overlay_for(parent.research_subject)
    event = overlay.buying_event if overlay else None
    gaps = event_gaps(event, overlay.research.evidence if overlay else (),
                     overlay.targets if overlay else (), as_of=run.as_of, client_name=run.client_name)
    coverage = _coverage_hold(run.coverage)
    if coverage:
        gaps.append(coverage)
    if gaps:
        return _as_watch_or_hold(lead, lead.seller_path, trace, '; '.join(gaps), watch=False)
    action = lead.next_action.model_copy(update={'blocked_by': None})
    updated = lead.model_copy(update={'lead_tier': LeadTier.LEAD_T2,
        'readiness': TIER_READINESS[LeadTier.LEAD_T2], 'next_action': action})
    return updated, _refresh_trace(updated, updated.seller_path, trace,
        'LEAD_T2: current early buying decision with sourced fit, supplier, responsible owner, email and mobile; outreach permission unchanged'), False
