"""057 source/review authority probes; no fresh collection or lead promotion."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from agents.assess.ledger import build_assess_run
from agents.assess.reviewed_cases import ReviewedCases, ReviewedSubject
from agents.golden_press.external_product_projection import build_external_product_document
from agents.golden_press.notice_bridge import NoticeReadContext, digest
from agents.leadgen.press import run_press
from agents.leadgen.targets import LeadResearch, LeadTarget
from tests.test_research_subjects import research_sweep, NOW, BINDING


def fixture(monkeypatch, tmp_path):
    sweep, profile = research_sweep(monkeypatch)
    initial, _, _ = build_assess_run('NETSCOUT', sweep, profile, BINDING, as_of=NOW)
    overlays = []
    for i, subject in enumerate(initial.research.items):
        research = LeadResearch(status='investigation' if i < 4 else 'deprioritized',
            priority=i < 4, rationale=f'Exact source reason {i}',
            buyer_requirement=subject.evidence[0].excerpt, fit_hypothesis='Possible fit remains unconfirmed',
            route='Confirm the published route', why_now='Resolve source scope before advancing',
            next_ask=f'Current source question {i}?', open_questions=('Who owns the actual product decision?',),
            reviewed_by='Synthetic authority fixture', reviewed_at=NOW, evidence=subject.evidence)
        targets = (LeadTarget(name='Synthetic source contact', role='Published contact', organization=subject.agency,
            source_kind='government_published', source_url=subject.source_url, email='fixture@example.gov',
            phone='202-555-0100', contact_status='Synthetic published-channel fixture',
            route='Confirm the permitted route', reason_to_contact='Resolve the source scope',
            next_ask=research.next_ask, authority_boundary='Purchasing authority unconfirmed',
            evidence=subject.evidence),) if i == 0 else ()
        overlays.append(ReviewedSubject(subject_id=subject.subject_id,
                                       source_sha256=subject.source_sha256, research=research, targets=targets))
    book = ReviewedCases(schema_version='reviewed_cases.v2', client_name='NETSCOUT', subjects=tuple(overlays))
    run, _, _ = build_assess_run('NETSCOUT', sweep, profile, BINDING, as_of=NOW, reviewed_cases=book)
    receipt = run_press(assess=run, client_name='NETSCOUT', reviewed_cases=book)
    companion = {'status':'complete', 'assess_run_id':run.run_id,
                 'assessment':{'run':run.model_dump(mode='json')}, 'receipt':receipt.model_dump(mode='json')}
    # Exercise actual unchanged-file checking with native immutable Assess/Press
    # objects. This fixture isolates the read context loader, not its authority.
    context = NoticeReadContext.__new__(NoticeReadContext)
    context.current_assess_run = run
    context.current_reviewed_cases = book
    context.client_name = 'NETSCOUT'
    context.as_of = NOW
    context.gaps = []
    context._projection_inputs = None
    evidence = tmp_path / 'current-inputs.json'
    evidence.write_text(json.dumps({'run':run.model_dump(mode='json'), 'review':book.model_dump(mode='json')}))
    context.files = {str(evidence):hashlib.sha256(evidence.read_bytes()).hexdigest()}
    graph = {'records':[], 'qualified_opportunity_records':[]}
    context._graph_sha256 = digest(graph)
    return context, graph, companion, evidence


def document(context, graph, companion):
    return build_external_product_document(market_map={}, graph_payload=graph,
        evidence_pack={}, profile={}, client_name='NETSCOUT', slug='netscout',
        as_of=NOW.date().isoformat(), leadgen=companion, notice_context=context)


def research_rows(doc):
    return [r for slot in doc.slots[1:] for r in slot.records if r.get('research_subject')]


def test_all_parent_only_sources_survive_once_with_three_reviewed_priorities(monkeypatch,tmp_path):
    context, graph, companion, _ = fixture(monkeypatch,tmp_path)
    original = json.dumps(companion,sort_keys=True)
    doc = document(context,graph,companion)
    rows = research_rows(doc)
    assert len(doc.slots)==8
    assert len(rows)==len(context.current_assess_run.research.items)
    assert len({r['research_subject']['subject_id'] for r in rows})==len(rows)
    assert len(doc.slots[0].records)==3
    slots={s.slot_id:s for s in doc.slots}
    for ref in doc.slots[0].records:
        assert any(r['record_key']==ref['reference_key'] for r in slots[ref['reference_slot_id']].records)
    for row in rows:
        subject=row['research_subject']
        assert row['research_as_of']==context.current_assess_run.as_of.isoformat()
        assert json.loads(subject['reviewed_overlay_json'])['research']==row['research']
        assert row['owning_slot_id']==('future-forecasts' if subject['source_kind']=='forecast' else 'agency-spending')
        assert not row.get('lead_rows') and not row.get('current_notice_admitted')
    deprioritized=[r for r in rows if r['research']['status']=='deprioritized']
    assert deprioritized and all(r['research']['rationale'] for r in deprioritized)
    assert not {r['record_key'] for r in deprioritized}.intersection(r['reference_key'] for r in doc.slots[0].records)
    assert json.dumps(companion,sort_keys=True)==original


@pytest.mark.parametrize('mutation', ['source_sha','detached_flag','parent_missing','duplicate_parent','receipt_clock','run_clock','run_id','client','scope','target'])
def test_altered_companion_cannot_supply_current_research(monkeypatch,tmp_path,mutation):
    context,graph,c,_=fixture(monkeypatch,tmp_path)
    parents=c['receipt']['parents'];p=next(p for p in parents if p.get('research_subject'))
    if mutation=='source_sha':p['research_subject']['source_sha256']='a'*64
    elif mutation=='detached_flag':p['research']['next_ask']='Detached manufactured next ask'
    elif mutation=='parent_missing':parents.remove(p)
    elif mutation=='duplicate_parent':parents.append(deepcopy(p))
    elif mutation=='receipt_clock':c['receipt']['as_of']=(NOW+timedelta(days=1)).isoformat()
    elif mutation=='run_clock':c['assessment']['run']['as_of']=(NOW+timedelta(days=1)).isoformat()
    elif mutation=='run_id':c['assess_run_id']='another-run'
    elif mutation=='client':c['assessment']['run']['client_name']='OTHER'
    elif mutation=='scope':c['assessment']['run']['scope']={'mode':'agency','agencies':[{'name':'OTHER'}]}
    elif mutation=='target':p['targets']=[{'name':'Injected contact'}]
    try:
        doc=document(context,graph,c)
    except ValueError:
        # Existing outer Press validation can refuse malformed typed receipts.
        return
    assert not research_rows(doc) and not doc.slots[0].records
    assert doc.graph_receipt['lead_projection_gaps']


@pytest.mark.parametrize('change',['review','target','review_scope','review_client','removed_review','context_changed','context_missing'])
def test_current_review_and_context_must_still_match(monkeypatch,tmp_path,change):
    context,graph,c,path=fixture(monkeypatch,tmp_path)
    book=context.current_reviewed_cases
    if change=='review':
        r=book.subjects[0];r=r.model_copy(update={'research':r.research.model_copy(update={'next_ask':'New approved question'})})
        context.current_reviewed_cases=book.model_copy(update={'subjects':(r,*book.subjects[1:])})
    elif change=='target':
        r=book.subjects[0];t=r.targets[0].model_copy(update={'email':'replacement@example.gov'})
        context.current_reviewed_cases=book.model_copy(update={'subjects':(r.model_copy(update={'targets':(t,)}),*book.subjects[1:])})
    elif change=='review_scope':context.current_reviewed_cases=book.model_copy(update={'scope_designator':'agency_other'})
    elif change=='review_client':context.current_reviewed_cases=book.model_copy(update={'client_name':'OTHER'})
    elif change=='removed_review':context.current_reviewed_cases=book.model_copy(update={'subjects':book.subjects[1:]})
    elif change=='context_changed':path.write_text('{}')
    elif change=='context_missing':context=None
    doc=document(context,graph,c)
    assert not research_rows(doc) and not doc.slots[0].records


def test_existing_owner_is_reused_and_graph_fact_cannot_supply_review(monkeypatch,tmp_path):
    context,graph,c,_=fixture(monkeypatch,tmp_path)
    subject=next(s for s in context.current_assess_run.research.items if s.source_kind=='award')
    graph['records']=[{'record_id':subject.source_record_id,'url':str(subject.source_url),
        'title':'Saved graph title','evidence_class':'competitive_historical','summary':'Graph claim',
        'research':{'priority':True,'next_ask':'Unbound graph question'}}]
    context._graph_sha256=digest(graph)
    before=deepcopy(graph)
    doc=document(context,graph,c)
    row=next(r for r in doc.slots[3].records if r.get('research_subject'))
    assert row['title']==subject.title and row['owning_slot_id']=='competitors'
    assert row['research']['next_ask']!='Unbound graph question'
    assert sum(r.get('research_subject',{}).get('subject_id')==subject.subject_id for r in research_rows(doc))==1
    assert graph==before


def test_same_id_different_official_url_does_not_borrow_existing_owner(monkeypatch,tmp_path):
    context,graph,c,_=fixture(monkeypatch,tmp_path)
    subject=next(s for s in context.current_assess_run.research.items if s.source_kind=='award')
    graph['records']=[{'record_id':subject.source_record_id,'url':'https://other.gov/unrelated',
        'title':'Different source','evidence_class':'competitive_historical'}]
    context._graph_sha256=digest(graph)
    doc=document(context,graph,c)
    assert not doc.slots[3].records[0].get('research_subject')
    assert any(r.get('research_subject',{}).get('subject_id')==subject.subject_id for r in doc.slots[2].records)


def test_native_disk_loaded_context_carries_parent_only_research(monkeypatch,tmp_path):
    from agents.assess import ledger
    from agents.assess.approval import current_assess_binding
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm
    from tests.test_native_notice_bridge import write
    seeded, _, _, _ = fixture(monkeypatch,tmp_path)
    book=seeded.current_reviewed_cases
    sweep,profile=research_sweep(monkeypatch)
    review=tmp_path/'data/review'
    sweep_path=tmp_path/'data/cleaned/searches_netscout.json'
    write(sweep_path,sweep)
    write(tmp_path/'clients/netscout/profile.json',profile.model_dump(mode='json'))
    taxonomy=CapabilityTaxonomy(client_name='NETSCOUT',version=1,updated='2026-09-13',core=[TaxonomyTerm(term='packet capture')])
    write(tmp_path/'clients/netscout/capability_taxonomy.json',taxonomy.model_dump(mode='json'))
    binding=current_assess_binding('NETSCOUT',review_dir=str(review),sweep_path=str(sweep_path),sweep=sweep,profile=profile)
    initial,_,_=ledger.build_assess_run('NETSCOUT',sweep,profile,binding,as_of=NOW)
    old={r.subject_id:r for r in book.subjects}
    book=book.model_copy(update={'subjects':tuple(ReviewedSubject(subject_id=s.subject_id,
        source_sha256=s.source_sha256, research=old[s.subject_id].research.model_copy(update={'evidence':s.evidence}))
        for s in initial.research.items)})
    write(review/'netscout.reviewed_cases.json',book.model_dump(mode='json'))
    manifest=ledger.assess_projection_input_manifest('NETSCOUT',review_dir=review)
    run,diagnostics,index=ledger.build_assess_run('NETSCOUT',sweep,profile,binding,as_of=NOW,
                                                projection_inputs=manifest,reviewed_cases=book)
    ledger.persist_assess_run(run,binding,diagnostics,index,manifest,state_dir=tmp_path/'data/state/assess_runs')
    context=NoticeReadContext(tmp_path,'NETSCOUT','netscout',NOW)
    assert not context.gaps,context.gaps
    companion={'status':'complete','assess_run_id':run.run_id,'assessment':{'run':run.model_dump(mode='json')},
               'receipt':run_press(assess=run,client_name='NETSCOUT',reviewed_cases=book).model_dump(mode='json')}
    doc=document(context,{'records':[]},companion)
    assert len(research_rows(doc))==len(run.research.items)
    assert len(doc.slots[0].records)==3
    # Changing the review on disk invalidates the already-loaded context.
    changed=book.model_dump(mode='json');changed['subjects'][0]['research']['rationale']='Changed after Assess'
    write(review/'netscout.reviewed_cases.json',changed)
    assert not research_rows(document(context,{'records':[]},companion))


def test_separate_investigations_and_attributed_context_survive_projection(monkeypatch,tmp_path):
    from tests.test_shared_qualification import _expansion_overlay
    from agents.assess.contracts import ResearchSubject
    from agents.assess.research_subjects import canonical
    context,graph,c,_=fixture(monkeypatch,tmp_path)
    template_subject,template=_expansion_overlay()
    subject=next(s for s in context.current_assess_run.research.items if s.source_record_id==template_subject.source_record_id)
    old=next(r for r in context.current_reviewed_cases.subjects if r.subject_id==subject.subject_id)
    overlay=old.model_copy(update={'research':old.research.model_copy(update={'status':'known_opportunity','priority':True}),
                                  'investigations':template.investigations})
    source=ResearchSubject.model_validate({**subject.model_dump(mode='json'),'reviewed_overlay_json':canonical(overlay.model_dump(mode='json'))})
    run=context.current_assess_run
    run=run.model_copy(update={'research':run.research.model_copy(update={'items':tuple(source if s.subject_id==source.subject_id else s for s in run.research.items)})})
    book=context.current_reviewed_cases.model_copy(update={'subjects':tuple(overlay if r.subject_id==overlay.subject_id else r for r in context.current_reviewed_cases.subjects)})
    context.current_assess_run=run;context.current_reviewed_cases=book
    c['assessment']['run']=run.model_dump(mode='json')
    c['receipt']=run_press(assess=run,client_name='NETSCOUT',reviewed_cases=book).model_dump(mode='json')
    doc=document(context,graph,c)
    row=next(r for r in research_rows(doc) if r['source_id']==subject.source_record_id)
    retained=json.loads(row['research_subject']['reviewed_overlay_json'])
    assert retained==overlay.model_dump(mode='json')
    assert retained['research']['status']=='known_opportunity'
    assert retained['investigations'][0]['research']['status']=='investigation'
    assert retained['investigations'][0]['customer_statements'][0]['event_date'] is None
    assert retained['investigations'][0]['customer_statements'][0]['relative_date_text']=='next week'
    assert not row.get('lead_rows')


@pytest.mark.parametrize('stale_graph',[False,True])
def test_current_forecast_visuals_cannot_inherit_graph_amount_or_clock(monkeypatch,tmp_path,stale_graph):
    from agents.golden_press.external_product_render import _work_details
    context,graph,c,_=fixture(monkeypatch,tmp_path)
    subject=next(s for s in context.current_assess_run.research.items if s.source_kind=='forecast')
    source=json.loads(subject.source_payload_json)
    raw={'record_id':subject.source_record_id,'url':str(subject.source_url),
         'title':subject.title,'evidence_class':'forecast'}
    if stale_graph:
        raw.update(estimated_value_range='$999M',response_deadline='2099-12-31',
            window_state='2099 stale buying window',source_as_of='2099-12-30',
            service_fit='Old graph manufactured fit',commercial_route='Old graph manufactured route',
            agency='Old graph agency')
    graph['records']=[raw];context._graph_sha256=digest(graph)
    before=deepcopy(graph)
    doc=document(context,graph,c)
    row=next(r for r in doc.slots[6].records if r['source_id']==subject.source_record_id)
    assert row['value']==source['estimated_value_range']
    assert row['response_date'] is None and row['source_as_of'] is None
    assert row['summary']==subject.evidence[0].excerpt
    assert source['anticipated_solicitation'] in row['window_state']
    assert not row.get('service_fit') and not row.get('commercial_route')
    points=[p for visual in doc.slots[6].visuals for p in visual.get('points',[]) if p['record_key']==row['record_key']]
    assert len(points)==1
    assert points[0]['magnitude_label']==source['estimated_value_range']
    assert points[0]['timing']==row['window_state']
    assert all(token not in json.dumps(doc.to_dict()) for token in ('$999M','2099-12-31','Old graph'))
    assert all(token not in json.dumps(_work_details(doc)) for token in ('$999M','2099-12-31','Old graph'))
    assert graph==before
