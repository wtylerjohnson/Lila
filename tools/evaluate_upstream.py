"""Bounded real-record replay through native producers, Assess and LeadGen.

Diagnostic only: no strategy approvals, HTTP, metered enrichment, outreach or
client release. The capture clock is explicit; never backdate a current capture.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from agents.assess.ledger import build_assess_run
from agents.assess.upstream import coverage_matrix, stamp
from agents.leadgen.press import run_press
from agents.leadgen.press_html import render_html
from agents.schemas import CapabilityProfile
from tools.api.forecasts.dhs_apfs import map_record
from tools.api.forecasts.research import discover_forecasts, serialize_research
from tools.api.forecasts.store import record_payload
from tools.api.forecasts.contacts import contact_record
from tools.capability import ClientProfile
from tools.relevance.taxonomy import CapabilityTaxonomy
from tools.artifacts import atomic_write_json
from tools.atomic_io import atomic_write_text


def replay(raw, capability_input, *, captured_at, as_of):
    if captured_at > as_of:
        raise ValueError('capture postdates simulated date; retrospective leakage refused')
    profile = ClientProfile.model_validate(capability_input['profile'])
    taxonomy = CapabilityTaxonomy.model_validate(capability_input['taxonomy'])
    mapped, failures = [], []
    for r in raw:
        try:
            mapped.append(map_record(r, retrieved_at=captured_at))
        except (ValueError, TypeError) as exc:
            failures.append({'id': r.get('id'), 'error': str(exc)})
    found = discover_forecasts(mapped, CapabilityProfile(client_name=profile.client_name,
        naics_codes=profile.naics_boundary), taxonomy=taxonomy)
    forecasts = {'parsed_inventory': [record_payload(r) for r in mapped],
                 'research_candidates': [serialize_research(c) for c in found],
                 'total_records': len(mapped), 'mapping_failures': failures,
                 'retrieved_at': captured_at.isoformat(),
                 'coverage_boundary': 'DHS APFS captured inventory only; other sources not searched by this replay'}
    searches = {'client': profile.client_name, 'generated_at': as_of.isoformat(),
                'search_scope': {'agencies':[{'abbr':'DHS', 'name':'Department of Homeland Security'}], 'mode':'focus'},
                'results': {'forecast_signals':forecasts, 'upstream_vocabulary':taxonomy.model_dump(mode='json')}}
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    binding = {'version':1, 'scope_designator':'agency_dhs',
               'sweep_sha256':digest(searches), 'profile_sha256':digest(capability_input['profile']),
               'sweep_artifact':'diagnostic-forecast-sweep.json'}
    run, diagnostics, _ = build_assess_run(profile.client_name, searches, profile, binding, as_of=as_of)
    press = run_press(assess=run, client_name=profile.client_name)
    agencies = ['Department of Homeland Security', 'Missile Defense Agency',
                *sorted({r.component for r in mapped if r.component})]
    coverage = coverage_matrix(searches, agencies=agencies, research=run.research, parents=press.parents)
    records = []
    for c in found:
        r = c['record']; contacts = contact_record(record_payload(r))
        records.append({'id':r.source_id, 'url':r.url, 'title':r.title,
            'component':r.component, 'matched_capabilities':c['capability_terms'],
            'published_solicitation_estimate':r.anticipated_solicitation,
            'withdrawal_evidence':c.get('withdrawal_evidence'),
            'published_contacts':[v.model_dump(mode='json') for v in contacts['contacts']] if contacts else [],
            'status':'research_candidate_not_adjudicated', 'qualification':'not_established'})
    metrics = {'scope':'Veeam / DHS APFS only; MDA unsearched in this replay',
        'proof':'real captured records via native library replay; NOT Command Center execution or a client release',
        'as_of':as_of.isoformat(), 'captured_at_claim':captured_at.isoformat(),
        'capture_authority':'caller supplied; no promotion of source acquisition trust',
        'input_records':len(raw), 'parsed_records':len(mapped), 'parse_failures':failures,
        'research_candidates':len(found), 'native_research_parents':len(run.research.items),
        'qualified_buying_children':len(press.leads),
        'candidate_records_with_published_named_channel':sum(any(p.get('name') and (p.get('email') or p.get('phone')) for p in r['published_contacts']) for r in records),
        'useful_lead_yield':None, 'relevance_precision':None, 'market_recall':None,
        'actual_days_before_solicitation':None,
        'limitations':['No independent market census or adjudicated precision denominator.',
                       'Published POC channels do not establish authority or deliverability.',
                       'Estimated release dates are not actual solicitation publication dates.',
                       'No named buying owner, permitted conversation or Veeam conformity inferred.',
                       'Native strategy approval and fresh Command Center execution remain required.'],
        'records':records, 'assess_diagnostics':list(diagnostics)}
    return {'sweep.json': searches, 'assess.json':run.model_dump(mode='json'),
            'coverage.json':coverage, 'metrics.json':metrics}, render_html(press)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw-forecasts', type=Path, required=True)
    ap.add_argument('--capability-input', type=Path, required=True)
    ap.add_argument('--captured-at', required=True)
    ap.add_argument('--as-of', required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    captured, cutoff = stamp(args.captured_at), stamp(args.as_of)
    if not captured or not cutoff: ap.error('aware capture and as-of instants required')
    if args.output.exists(): ap.error('output must be a new directory; preserve previous replay')
    documents, html = replay(json.loads(args.raw_forecasts.read_text()),
        json.loads(args.capability_input.read_text()), captured_at=captured, as_of=cutoff)
    args.output.mkdir(parents=True)
    for name, value in documents.items(): atomic_write_json(str(args.output/name), value)
    atomic_write_text(args.output/'INTERNAL-native-preview.html', html)
    hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir()) if p.is_file()}
    atomic_write_json(str(args.output/'receipt.json'), {
        'input_sha256':hashlib.sha256(args.raw_forecasts.read_bytes()).hexdigest(),
        'capability_input_sha256':hashlib.sha256(args.capability_input.read_bytes()).hexdigest(),
        'artifacts':hashes, 'external_release':False})
    print(json.dumps({k:documents['metrics.json'][k] for k in
        ('input_records','parsed_records','research_candidates','native_research_parents','qualified_buying_children')}, indent=2))


if __name__ == '__main__': main()
