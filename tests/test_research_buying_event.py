"""Adversarial research-to-lead proof. Fixtures are not measured client yield."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
from copy import deepcopy
import json
import pytest
from agents.assess.contracts import EvidenceRef, SourceCoverage, CoverageStatus, EvidenceKind
from agents.assess.source_clock import acquisition_clock
from agents.assess.reviewed_cases import ReviewedSubject, ReviewedCases
from agents.assess.research_subjects import make_subject
from agents.assess.ledger import build_assess_run
from agents.leadgen.targets import LeadResearch, LeadTarget
from agents.leadgen.research_event import ResearchBuyingEvent, event_gaps, contact_gaps
from agents.leadgen.press import run_press
from agents.leadgen.press_html import render_html
from agents.leadgen.business_proof import score
from tests.test_upstream_research import row, sweep, PROFILE, NOW


def ref(key, text, *, government=True):
    sha=hashlib.sha256(text.encode()).hexdigest()
    # Native fact-bank acquisition is supported already; budget capture time is not upgraded.
    clock=acquisition_clock('2026-09-15T12:00:00+00:00',basis='horizon_fact_bank',component='horizon_fact',field='fact.retrieved_at',binding=sha)
    return EvidenceRef(evidence_id=key,tier='program',kind='agency_announcement',source_name='Fixture source',
        source_url='https://www.mda.mil/event' if government else 'https://www.veeam.com/product',
        retrieved_at=datetime(2026,9,15,12,tzinfo=timezone.utc),source_acquisition=clock,
        record_hash=sha,excerpt=text,primary_source=True,supports=('requirement','timing','buyer','access'))


def evidence_case():
    texts={
      'need':'EVENT-42 requires VMware data recovery for the next platform deployment.',
      'decision':'EVENT-42 VMware data recovery selection decision is scheduled for October 1, 2026.',
      'fit':'Veeam provides VMware data recovery for virtual workloads.',
      'seller_route':'EVENT-42 VMware data recovery: Example Supplier is an eligible reseller authorized to supply Veeam software.',
      'owner':'EVENT-42 VMware data recovery: Jordan Example in Enterprise IT owns the technical requirement.',
    }
    refs=[ref(k,t,government=k!='fit') for k,t in texts.items()]
    raw_contact={'name':'Jordan Example','email':'jordan@example.mil','email_status':'verified','phone':'+12025550101',
                 'phone_type':'mobile','phone_status':'valid_number','dnc_status':'not_found','verified_at':'2026-09-15T12:00:00+00:00'}
    contactref=ref('contact',json.dumps(raw_contact))
    target=LeadTarget(name='Jordan Example',role='Technical owner',organization='MDA Enterprise IT',
        source_kind='government_published',source_url=contactref.source_url,email='jordan@example.mil',phone='+12025550101',
        contact_status='Verified source contact',route='Example Supplier',reason_to_contact='Owns the requirement',
        next_ask='Which VMware workloads and recovery objectives are in the October selection?',
        authority_boundary='Technical owner; no permission to contact',evidence=(contactref,),
        channel_verification=dict(evidence_id='contact',verified_at='2026-09-15T12:00:00Z',email_status='verified',
            phone_type='mobile',phone_status='valid_number',dnc_status='not_found'))
    event=ResearchBuyingEvent(event_id='EVENT-42',requirement='VMware data recovery',buyer_office='Enterprise IT',
        decision_date='2026-10-01',supplier='Example Supplier',seller_path='channel_reseller',target_name=target.name,
        claims=[dict(kind=k,evidence_id=k,quote=t) for k,t in texts.items()])
    return event,refs,target


def make_run(event,refs,target):
    source=row(); subject=make_subject('program',source,as_of=NOW)
    research=LeadResearch(status='investigation',priority=True,rationale='Investigate documented decision',
        buyer_requirement=event.requirement,fit_hypothesis='Veeam capability documented',route=event.supplier,
        why_now='Selection October 1, 2026',next_ask=target.next_ask,open_questions=('Deployment sizing',),
        reviewed_by='Fixture',reviewed_at=NOW,evidence=tuple(refs))
    book=ReviewedCases(schema_version='reviewed_cases.v2',client_name='Veeam',scope_designator='all',
        subjects=(ReviewedSubject(subject_id=subject.subject_id,source_sha256=subject.source_sha256,
            research=research,targets=(target,),buying_event=event),))
    binding={'version':1,'scope_designator':'all','profile_sha256':'fixture','sweep_sha256':'fixture','sweep_artifact':'fixture.json'}
    run,_,_=build_assess_run('Veeam',sweep(source),PROFILE,binding,reviewed_cases=book,as_of=NOW)
    return run.model_copy(update={'coverage':(SourceCoverage(source='sam.gov',lane='live',status='complete',required=True),)})


def test_real_bridge_accepts_early_event_without_live_solicitation_and_keeps_permission_none():
    event,refs,target=evidence_case(); run=make_run(event,refs,target)
    receipt=run_press(assess=run,client_name='Veeam')
    assert not run.live.records
    assert len(receipt.leads)==1 and receipt.leads[0].lead_tier=='LEAD_T2'
    assert receipt.leads[0].communication_permission=='none'
    assert receipt.parents[0].research_subject.source_sha256==run.research.items[0].source_sha256
    assert len(receipt.parents[0].lead_ids)==1
    assert score(run,receipt)['accepted_unique_buying_events']==1
    html=render_html(receipt)
    assert target.next_ask in html and target.email in html and target.phone in html


@pytest.mark.parametrize('defect',['budget','forecast','untrusted','past','wrong_event','wrong_requirement','wrong_owner',
                                  'generic_title','vehicle_only','wrong_supplier','unverified_email','office_phone','dnc','missing_mobile'])
def test_incomplete_or_false_receipts_never_promote(defect):
    event,refs,target=evidence_case()
    if defect in ('budget','forecast'):
        refs[0]=refs[0].model_copy(update={'kind':EvidenceKind.BUDGET if defect=='budget' else EvidenceKind.AGENCY_FORECAST})
    if defect=='untrusted':refs[0]=refs[0].model_copy(update={'source_acquisition':None,'retrieved_at':None})
    if defect=='past':event=event.model_copy(update={'decision_date':NOW.date()})
    if defect in ('wrong_event','wrong_requirement','wrong_owner','wrong_supplier'):
        event=event.model_copy(update={{'wrong_event':'event_id','wrong_requirement':'requirement','wrong_owner':'target_name','wrong_supplier':'supplier'}[defect]:'Unrelated other'})
    if defect in ('generic_title','vehicle_only'):
        key='owner' if defect=='generic_title' else 'seller_route'
        text='EVENT-42 VMware data recovery: Jordan Example is a director in Enterprise IT.' if key=='owner' else 'EVENT-42 VMware data recovery: Example Supplier holds a vehicle.'
        refs=[ref(key,text) if r.evidence_id==key else r for r in refs]
        event=event.model_copy(update={'claims':tuple(c.model_copy(update={'quote':text}) if c.kind==key else c for c in event.claims)})
    if defect in ('unverified_email','office_phone','dnc'):
        change={'unverified_email':{'email_status':'unknown'},'office_phone':{'phone_type':'office'},'dnc':{'dnc_status':'listed'}}[defect]
        target=target.model_copy(update={'channel_verification':target.channel_verification.model_copy(update=change)})
    if defect=='missing_mobile':target=target.model_copy(update={'phone':None})
    run=make_run(event,refs,target);receipt=run_press(assess=run,client_name='Veeam')
    assert not any(l.lead_tier in {'LEAD_T1','LEAD_T2'} for l in receipt.leads)
    assert score(run,receipt)['accepted_unique_buying_events']==0


def test_coverage_still_blocks_and_zero_cost_ratio_is_undefined():
    event,refs,target=evidence_case();run=make_run(event,refs,target)
    run=run.model_copy(update={'coverage':(*run.coverage, SourceCoverage(source='failed',lane='horizon',status='failed',required=True),)})
    receipt=run_press(assess=run,client_name='Veeam')
    assert receipt.leads[0].lead_tier=='HOLD'
    result=score(run,receipt,elapsed_seconds=12,incremental_cost_usd=0)
    assert result['cost_per_accepted_event_usd'] is None and result['seconds_per_accepted_event'] is None
    assert 'required Assess coverage' in receipt.leads[0].next_action.blocked_by


def test_more_contacts_do_not_inflate_unique_event_count():
    event,refs,target=evidence_case();run=make_run(event,refs,target)
    receipt=run_press(assess=run,client_name='Veeam')
    doubled=receipt.model_copy(update={'leads':receipt.leads*2})
    assert score(run,doubled)['accepted_unique_buying_events']==1


def test_legacy_contact_roundtrip_unchanged():
    _,_,target=evidence_case();raw=target.model_dump(mode='json');raw.pop('channel_verification')
    assert LeadTarget.model_validate(raw).model_dump(mode='json')==raw


def test_hidden_counterevidence_and_negated_route_block():
    event,refs,target=evidence_case()
    refs.append(ref('counter','EVENT-42 VMware data recovery has been canceled.'))
    assert event_gaps(event,refs,(target,),as_of=NOW,client_name='Veeam')
    run=make_run(event,refs,target)
    assert run_press(assess=run,client_name='Veeam').leads[0].lead_tier=='HOLD'
    event,refs,target=evidence_case()
    original=next(c for c in event.claims if c.kind=='seller_route')
    text=original.quote.replace('is an eligible','is not an eligible')
    refs=[ref('seller_route',text) if r.evidence_id=='seller_route' else r for r in refs]
    event=event.model_copy(update={'claims':tuple(c.model_copy(update={'quote':text}) if c.kind=='seller_route' else c for c in event.claims)})
    assert event_gaps(event,refs,(target,),as_of=NOW,client_name='Veeam')


def test_cannot_relabel_provider_office_number_as_mobile():
    event,refs,target=evidence_case()
    source=json.loads(target.evidence[0].excerpt);source['phone_type']='office'
    target=target.model_copy(update={'evidence':(ref('contact',json.dumps(source)),)})
    assert any('disagree' in gap for gap in contact_gaps(target,NOW))


def test_verified_document_capture_does_not_upgrade_original_research_clock():
    from agents.assess.document_evidence import captured_evidence
    from agents.assess.source_clock import acquired_at
    r=row();text=r['description']
    r['source']='agency_program_documents'
    r['document_evidence']={'document':{'status':'captured','http_status':200,'method':'GET',
        'finished_at':'2026-09-15T12:00:00+00:00','raw_sha256':'a'*64,'final_url':r['canonical_url'],
        'attempts':[{'state':'success','tls_verified':True}]},'extraction_sha256':hashlib.sha256(text.encode()).hexdigest()}
    evidence=captured_evidence(r,NOW)
    assert acquired_at(evidence).date().isoformat()=='2026-09-15'
    assert acquired_at(make_subject('program',r,as_of=NOW).evidence[0]) is None
    assert evidence.kind=='budget'  # acquisition is not enacted funding
    for field,value in [('status','failed'),('http_status',500),('raw_sha256',None),('finished_at','2026-09-20T12:00:00Z')]:
        bad=deepcopy(r);bad['document_evidence']['document'][field]=value
        assert captured_evidence(bad,NOW) is None
    bad=deepcopy(r);bad['description']+=' altered'
    assert captured_evidence(bad,NOW) is None


def test_supplement_input_exact_identity_and_hash_binding(tmp_path,monkeypatch):
    from agents.assess.research_inputs import read_inputs,fingerprint
    from agents.assess.investigation_inputs import input_binding
    event,refs,target=evidence_case();subject=make_subject('program',row(),as_of=NOW)
    path=tmp_path/'inputs.json';monkeypatch.setenv('LILA_INVESTIGATION_SUPPLEMENTS_PATH',str(path))
    record={'source_system':subject.source_system,'source_record_id':subject.source_record_id,
        'captured_at':'2026-09-15T12:00:00+00:00','evidence':[r.model_dump(mode='json') for r in (*refs,*target.evidence)],
        'contacts':[target.model_dump(mode='json')]}
    book={'version':'research-inputs.v1','client_name':'Veeam','records':[record]}
    path.write_text(json.dumps(book));before=input_binding((subject,),PROFILE,NOW)
    rows,receipt=read_inputs('Veeam',(subject,),NOW)
    assert subject.subject_id in rows and receipt['sha256']==fingerprint('Veeam')
    book['records'][0]['source_record_id']='unrelated'
    path.write_text(json.dumps(book))
    assert read_inputs('Veeam',(subject,),NOW)[0]=={}
    assert before!=input_binding((subject,),PROFILE,NOW)
    book['records'][0]['source_record_id']=subject.source_record_id
    book['records'][0]['captured_at']='2026-09-20T12:00:00+00:00';path.write_text(json.dumps(book))
    with pytest.raises(ValueError,match='cutoff'):read_inputs('Veeam',(subject,),NOW)


def test_review_quality_accepts_evidenced_child_and_rejects_false_promotion():
    from agents.leadgen.quality_baseline import check_reviewed_quality
    event,refs,target=evidence_case();run=make_run(event,refs,target)
    overlay=ReviewedSubject.model_validate_json(run.research.items[0].reviewed_overlay_json)
    book=ReviewedCases(schema_version='reviewed_cases.v2',client_name='Veeam',scope_designator='all',subjects=(overlay,))
    receipt=run_press(assess=run,client_name='Veeam')
    assert check_reviewed_quality(receipt,book)['passed']
    bad=overlay.model_copy(update={'buying_event':None})
    with pytest.raises(ValueError,match='incorrectly minted'):
        check_reviewed_quality(receipt,book.model_copy(update={'subjects':(bad,)}))


def test_ordinary_companion_writes_real_native_output_and_no_pointer(tmp_path,monkeypatch):
    from agents.leadgen.business_proof import write_sweep_companion
    import tools.capability as capability
    path=tmp_path/'searches_veeam.json';path.write_text(json.dumps(sweep(row())))
    profile=tmp_path/'profile.json';profile.write_text(PROFILE.model_dump_json())
    monkeypatch.setattr(capability,'require_profile',lambda _:PROFILE)
    monkeypatch.setattr(capability,'profile_path',lambda _:str(profile))
    result=write_sweep_companion('Veeam',path)
    data=json.loads(open(result).read())
    assert data['accepted_unique_buying_events']==0
    assert data['zero_denominator_note']
    assert (Path(result).parent/'action-sheets.html').is_file()
    assert (Path(result).parent/'press.json').is_file()
    assert not list(tmp_path.rglob('*.current.json'))
    assert 'NOT RUN' in write_sweep_companion('Veeam',tmp_path/'absent.json')


def test_model_event_claim_passages_bind_real_source_ids(monkeypatch):
    from agents.assess import investigations as worker
    from agents.decisions import maxplan_cli
    from tests.test_source_conversation import draft
    event,refs,target=evidence_case()
    bundle={'source':row(),'client':PROFILE.model_dump(mode='json'),
            'evidence':[r.model_dump(mode='json') for r in refs],'contacts':{}}
    proposed=draft(bundle)
    proposed['evidence']=[{'passage_ids':['E1:P1']}]
    proposed['buying_event']=event.model_dump(mode='json')
    proposed['buying_event']['claims']=[{'kind':c.kind,'passage_ids':[f'E{i}:P1']} for i,c in enumerate(event.claims,1)]
    def respond(prompt,**kwargs):
        assert '"required":["kind","passage_ids"]' in prompt
        return json.dumps(proposed)
    monkeypatch.setattr(maxplan_cli,'run_claude',respond)
    monkeypatch.setenv('LILA_LLM_ROUTE','max')
    result=worker.synthesize(bundle)
    assert [c.evidence_id for c in result.buying_event.claims]==[r.evidence_id for r in refs]
    assert [c.quote for c in result.buying_event.claims]==[r.excerpt for r in refs]


def test_contact_lint_ignores_large_inert_download_but_still_checks_visible_email():
    from agents.reports.lint import lint_contact_rendering
    html='<a download="source.json" href="data:application/json;base64,'+'A'*250000+'">Download source</a><p>person@agency.gov</p>'
    result=lint_contact_rendering(html)
    assert not result.ok and len(result.violations)==1
    assert result.violations[0].rule=='ungraded_contact'
    assert 'person@agency.gov' in result.violations[0].excerpt


def test_count_lint_distinguishes_publisher_quotes_dates_and_authored_totals():
    from agents.reports.lint import lint_counts,lint_emdash
    assert lint_counts('<blockquote data-thirdparty="1">238 Missile Defense Agency</blockquote>').ok
    assert lint_counts('<p>Published on 2026-08-27 with an estimated solicitation release.</p>').ok
    assert not lint_counts('<p>27 solicitations</p>').ok
    assert lint_emdash('<blockquote data-thirdparty="1">Source title—original</blockquote>').ok
    assert not lint_emdash('<p>Authored—copy</p>').ok
