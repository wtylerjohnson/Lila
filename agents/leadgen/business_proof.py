"""Fixed, inspectable lead-quality scorecard and ordinary-run internal companion."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

from .enums import LeadTier
from .research_event import contact_gaps, event_gaps, compact
from .research_bridge import overlay_for

RUBRIC = 'unique-qualified-buying-events.v1'


def score(run, receipt, *, elapsed_seconds=None, incremental_cost_usd=None):
    from .from_assess import _coverage_hold
    coverage = _coverage_hold(run.coverage)
    rows, accepted, complete_contacts = [], set(), set()
    leads = {p.assessment_id: [l for l in receipt.leads if l.parent_assessment_id == p.assessment_id]
             for p in receipt.parents}
    for parent in receipt.parents:
        for target in (*parent.targets, *(t for l in leads[parent.assessment_id] for t in l.targets)):
            if not contact_gaps(target, run.as_of):
                complete_contacts.add((compact(target.name), compact(target.email), target.phone))
        overlay = overlay_for(parent.research_subject) if parent.research_subject else None
        event = overlay.buying_event if overlay else None
        gaps = []
        if parent.research_subject:
            gaps = event_gaps(event, overlay.research.evidence if overlay else (),
                overlay.targets if overlay else (), as_of=run.as_of, client_name=run.client_name)
        qualified = [l for l in leads[parent.assessment_id] if l.lead_tier in (LeadTier.LEAD_T1, LeadTier.LEAD_T2)]
        if not qualified:
            gaps.append('native qualifier did not accept a lead')
        elif not any(any(not contact_gaps(t, run.as_of) for t in l.targets) for l in qualified):
            gaps.append('accepted row lacks a source-verified email/mobile target')
        if coverage:
            gaps.append(coverage)
        event_key = ((compact(parent.agency), compact(event.event_id)) if event else
                     (compact(parent.agency), compact(parent.solicitation_number or parent.subject_id)))
        if not gaps:
            accepted.add(event_key)
        rows.append({'subject_id': parent.subject_id, 'source_id': parent.research_subject.source_record_id
                     if parent.research_subject else parent.notice_id,
            'title': parent.title, 'event_key': list(event_key), 'accepted': not gaps,
            'gaps': list(dict.fromkeys(gaps)), 'research_questions': list(overlay.research.open_questions) if overlay else [],
            'targets': [dict(name=t.name, has_email=bool(t.email), has_phone=bool(t.phone),
                             channel_gaps=contact_gaps(t, run.as_of), authority_boundary=t.authority_boundary)
                        for t in parent.targets]})
    count = len(accepted)
    return {'rubric': RUBRIC, 'as_of': run.as_of.isoformat(), 'assess_run_id': run.run_id,
        'accepted_unique_buying_events': count, 'contacts_with_verified_email_mobile': len(complete_contacts),
        'native_qualified_rows': sum(l.lead_tier in (LeadTier.LEAD_T1, LeadTier.LEAD_T2) for l in receipt.leads),
        'research_priorities': sum(bool(p.research and p.research.priority) for p in receipt.parents),
        'elapsed_seconds': elapsed_seconds, 'incremental_cost_usd': incremental_cost_usd,
        'seconds_per_accepted_event': elapsed_seconds / count if count and elapsed_seconds is not None else None,
        'cost_per_accepted_event_usd': incremental_cost_usd / count if count and incremental_cost_usd is not None else None,
        'zero_denominator_note': None if count else 'No accepted events; cost/time per accepted event is undefined, not zero.',
        'coverage': [c.model_dump(mode='json') for c in run.coverage], 'rows': rows,
        'boundary': 'Internal qualification proof. No Assess approval, Target activation, outreach or client release.'}


def write_sweep_companion(client, sweep_path):
    """Normal collection calls this, without modifying current Assess pointers."""
    from tools.capability import require_profile, profile_path
    from agents.assess.ledger import build_assess_run, scope_from_sweep, _scope_designator
    from .press import run_press
    from .press_html import render_html
    from tools.artifacts import atomic_write_json
    from tools.atomic_io import atomic_write_text
    path = Path(sweep_path)
    if not path.is_file():
        return 'NOT RUN: persisted sweep unavailable; no qualification count computed'
    data = json.loads(path.read_bytes())
    profile = require_profile(client)
    binding = {'version': 1, 'scope_designator': _scope_designator(scope_from_sweep(data)),
        'profile_sha256': hashlib.sha256(Path(profile_path(client)).read_bytes()).hexdigest(),
        'sweep_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'sweep_artifact': str(path)}
    run, diagnostics, _ = build_assess_run(client, data, profile, binding,
        as_of=datetime.fromisoformat(data['generated_at']))
    receipt = run_press(assess=run, client_name=client)
    output = path.parent / 'qualification' / binding['sweep_sha256']
    output.mkdir(parents=True, exist_ok=True)
    proof = score(run, receipt)
    proof.update(binding=binding, diagnostics=diagnostics)
    atomic_write_json(output / 'assess.json', run.model_dump(mode='json'))
    atomic_write_json(output / 'press.json', receipt.model_dump(mode='json'))
    atomic_write_json(output / 'scorecard.json', proof)
    atomic_write_text(str(output / 'action-sheets.html'), render_html(receipt))
    return str(output / 'scorecard.json')
