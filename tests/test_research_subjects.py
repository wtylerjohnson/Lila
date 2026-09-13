"""Native investigation lineage, source identity and legacy anti-downgrade proof."""
import copy
import json
from pathlib import Path
from datetime import datetime, timezone
import pytest
from agents.assess.contracts import AssessRun, ResearchSubject
from agents.assess.ledger import build_assess_run,persist_assess_run,load_current_assess_run,_run_projection,_run_identity,_digest
from agents.assess.research_subjects import make_subject
from agents.assess.source_clock import acquisition_clock,acquired_at
from agents.leadgen.from_assess import draft_lead_rows,coerce_assess_run
from agents.leadgen.export_assess import export_current_assess_run
from agents.leadgen.press import run_press,render_markdown
from agents.leadgen.press_html import render_html
from tools.api.research_awards import enrich_buyer_research
from tests.test_assess_ledger import _sweep,_profile,BINDING
from tests.test_netscout_forecast_research import ordinary_forecast_producer, rows, PROFILE

AWARDS=json.loads((Path(__file__).parent/'fixtures/netscout_dhs_awards.json').read_text())
NOW=datetime(2026,9,13,tzinfo=timezone.utc)

def research_sweep(monkeypatch):
    forecast,_=ordinary_forecast_producer(monkeypatch,rows())
    buyer={'buyers':[]}
    source_by_id={}
    for wrapped in AWARDS:
        raw=wrapped['data']; sid=raw['generated_unique_award_id']; source_by_id[sid]=raw
        buyer['buyers'].append({'agency':raw['awarding_agency']['toptier_agency']['name'],'buyer':raw['awarding_agency']['subtier_agency']['name'],'records':[{'kind':'award','award_id':raw['piid'],'generated_internal_id':sid,'description':raw['description'],'url':f'https://www.usaspending.gov/award/{sid}'}]})
    profile=_profile().model_copy(update={'client_name':'NETSCOUT'})
    enriched=enrich_buyer_research(buyer,profile,[],fetch=lambda url:source_by_id[url.rstrip('/').split('/')[-1]])
    sweep=_sweep();sweep.update(client='NETSCOUT',generated_at=NOW.isoformat())
    sweep['results'].update(forecast_signals=forecast,incumbent_buyer_map=enriched)
    return sweep,profile

def build(monkeypatch):
    sweep,profile=research_sweep(monkeypatch)
    return build_assess_run('NETSCOUT',sweep,profile,BINDING,as_of=NOW)

def test_real_producers_to_native_assess_export_and_parent_only_report(monkeypatch,tmp_path):
    run,diagnostics,index=build(monkeypatch)
    assert run.run_id.startswith('assess:v3:')
    assert len(run.research.items)==6
    assert len(run.live.records)==3
    path=persist_assess_run(run,BINDING,diagnostics,index,state_dir=tmp_path)
    assert load_current_assess_run('NETSCOUT','all',state_dir=tmp_path)['run']==run.model_dump(mode='json')
    exported=export_current_assess_run('NETSCOUT',scope='all',state_dir=tmp_path)
    receipt=run_press(assess=exported,client_name='NETSCOUT')
    parents=[p for p in receipt.parents if p.research_subject]
    assert len(parents)==6 and all(not p.lead_ids for p in parents)
    assert not any(l.parent_assessment_id in {p.assessment_id for p in parents} for l in receipt.leads)
    html=render_html(receipt); md=render_markdown(receipt)
    for p in parents:
        s=p.research_subject
        assert s.source_record_id in html and s.source_record_id in md and s.next_ask in html
        assert all(acquired_at(e) is None for e in s.evidence)
        assert all(e.source_acquisition.basis=='none' for e in s.evidence)
    award=next(s for s in run.research.items if '70B04C24F00000415' in s.source_record_id)
    raw=json.loads(award.source_payload_json)
    assert raw['period_of_performance']['end_date']=='2027-04-15'
    assert raw['period_of_performance']['potential_end_date'].startswith('2028-04-15')
    assert raw['total_obligation']==3126654.64 and raw['base_and_all_options']==4371483.8
    sirc=next(s for s in run.research.items if '70RTAC26FR0000009' in s.source_record_id)
    assert 'OR EQUAL' in sirc.evidence[0].excerpt
    assert 'Actual OEM and covered products' in sirc.open_questions
    assert path.read_bytes()

@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('seam',['raw','typed','envelope','persist'])
def test_research_cannot_be_relabelled_legacy(monkeypatch,tmp_path,version,seam):
    run,d,index=build(monkeypatch);raw=run.model_dump(mode='json')
    rid=f'assess:v{version}:'+run.run_id.rsplit(':',1)[-1]
    raw['run_id']=rid
    for lane in ('live','horizon','partners','research'):raw[lane]['run_id']=rid
    with pytest.raises(ValueError):
        if seam=='raw': AssessRun.model_validate(raw)
        elif seam=='typed': coerce_assess_run(run.model_copy(update={'run_id':rid}))
        elif seam=='envelope':coerce_assess_run({'schema_version':version,'run':raw})
        else:persist_assess_run(run.model_copy(update={'run_id':rid}),BINDING,d,index,state_dir=tmp_path)

@pytest.mark.parametrize('version',[1,2])
def test_original_legacy_population_read_identity_and_bytes(tmp_path,version):
    # Original version-specific serialization: no research field existed.
    profile=_profile();sweep=_sweep();run,d,index=build_assess_run('Testco',sweep,profile,BINDING)
    raw=run.model_dump(mode='json');raw.pop('research')
    from agents.assess.ledger import _legacy_projection
    if version==1:raw=_legacy_projection(raw)
    raw['run_id']='legacy-fixture'
    for lane in ('live','horizon','partners'):raw[lane]['run_id']='legacy-fixture'
    old=AssessRun.model_validate(raw)
    projection=_run_projection(as_of=old.as_of,scope=old.scope,profile_version=old.profile_version,live=old.live,horizon=old.horizon,partners=old.partners,coverage=old.coverage,approval_status=old.approval_status,approved_by=old.approved_by,approved_at=old.approved_at,partial_release_approved=old.partial_release_approved,requires_human_review=True,projection_inputs={},diagnostics=d,posting_index=index)
    if version==1:projection=_legacy_projection(projection)
    rid=_run_identity('Testco',BINDING,projection,schema_version=version);raw['run_id']=rid
    for lane in ('live','horizon','partners'):raw[lane]['run_id']=rid
    old=AssessRun.model_validate(raw)
    path=persist_assess_run(old,BINDING,d,index,state_dir=tmp_path)
    pointer=path.parent/'all.current.json';before=(path.read_bytes(),pointer.read_bytes())
    assert load_current_assess_run('Testco','all',state_dir=tmp_path)
    exported=export_current_assess_run('Testco',scope='all',state_dir=tmp_path)
    assert exported['run']['run_id']==rid and 'research' not in exported['run']
    assert (path.read_bytes(),pointer.read_bytes())==before

@pytest.mark.parametrize('change',['url','id','excerpt','hash','agency'])
def test_wrong_record_binding_refused(change):
    row=copy.deepcopy(AWARDS[0]['data']);row['url']=AWARDS[0]['sourceURL']
    subject=make_subject('award',row);raw=subject.model_dump(mode='json')
    if change=='url':raw['source_url']=AWARDS[1]['sourceURL']
    if change=='id':raw['source_record_id']='another-award'
    if change=='excerpt':raw['evidence'][0]['excerpt']='different scope'
    if change=='hash':raw['source_sha256']='a'*64
    if change=='agency':raw['agency']='Department of Defense'
    with pytest.raises(ValueError):ResearchSubject.model_validate(raw)

@pytest.mark.parametrize('value',[None,'','2026-09-12T12:00:00Z','2026-09-12',123])
def test_research_clock_cannot_claim_acquisition(value):
    c=acquisition_clock(value,basis='none',component='research_record',field='source_row.retrieved_at',binding='a'*64)
    assert c.status in {'missing','untrusted'}
    for basis in ('sam_notice_depth','horizon_fact_bank'):
        with pytest.raises(ValueError):acquisition_clock(value,basis=basis,component='research_record',field='source_row.retrieved_at',binding='a'*64)


def test_known_horizon_clock_cannot_replace_research_claim():
    row={**AWARDS[0]['data'],'url':AWARDS[0]['sourceURL'],'retrieved_at':'2026-09-12T12:00:00Z'}
    subject=make_subject('award',row);raw=subject.model_dump(mode='json')
    known=acquisition_clock(row['retrieved_at'],basis='horizon_fact_bank',component='horizon_fact',field='fact.retrieved_at',binding=subject.source_sha256)
    raw['evidence'][0].update(source_acquisition=known.model_dump(mode='json'),retrieved_at=row['retrieved_at'])
    with pytest.raises(ValueError,match='exact untrusted'):
        ResearchSubject.model_validate(raw)


@pytest.mark.parametrize('version',[1,2])
def test_legacy_evidence_cannot_smuggle_research_component(monkeypatch,version):
    run,d,index=build(monkeypatch);raw=run.model_dump(mode='json');raw.pop('research')
    rid=f'assess:v{version}:'+run.run_id.rsplit(':',1)[-1]
    raw['run_id']=rid
    for lane in ('live','horizon','partners'):raw[lane]['run_id']=rid
    e=raw['live']['records'][0]['authoritative_evidence'][0]
    e.update(source_acquisition=acquisition_clock(None,basis='none',component='research_record',field='source_row.retrieved_at',binding=e['record_hash']).model_dump(mode='json'),retrieved_at=None)
    with pytest.raises(ValueError,match='research source components|legacy Assess envelope'):
        coerce_assess_run({'schema_version':version,'run':raw})


def test_reviewed_parent_only_subjects_keep_distinct_asks_and_targets(monkeypatch):
    from agents.assess.reviewed_cases import ReviewedCases,ReviewedSubject
    from agents.leadgen.targets import LeadResearch,LeadTarget
    from agents.leadgen.quality_baseline import check_reviewed_quality
    from agents.assess.contracts import EvidenceRef,EvidenceKind,EvidenceTier
    from agents.assess.research_subjects import canonical
    sweep,profile=research_sweep(monkeypatch)
    run,d,index=build_assess_run('NETSCOUT',sweep,profile,BINDING,as_of=NOW)
    overlays=[]
    # Deliberately synthetic reviewer metadata tests the storage contract;
    # this fixture is never imported as an actual completed analyst review.
    for subject in run.research.items:
        if subject.source_kind!='award':continue
        tech='70B04C24F00000415' in subject.source_record_id
        ask='Which SKUs and option decisions does TechAnax cover?' if tech else 'Which products are supplied under NETSCOUT or equal?'
        route='TechAnax partner program manager; account ownership unknown' if tech else 'SIRC organization route info@sirc.net; named owner unknown'
        research=LeadResearch(status='investigation',priority=True,rationale='Existing award requires account clarification',buyer_requirement=subject.evidence[0].excerpt,fit_hypothesis='Actual products require confirmation',route=route,why_now='Recorded current period; continuation remains unknown',next_ask=ask,open_questions=subject.open_questions,reviewed_by='synthetic contract test reviewer',reviewed_at=NOW,evidence=subject.evidence)
        targets=()
        if tech:
            ev=EvidenceRef(evidence_id='test-company',tier=EvidenceTier.DISCOVERY,kind=EvidenceKind.WEB_LEAD,source_name='TechAnax published SEWP route',source_url='https://www.techanax.com/nasa-sewp-v',excerpt='Published partner route; account ownership unknown')
            targets=(LeadTarget(name='Bill Lytle',role='SEWP Program Manager',organization='TechAnax',source_kind='company_published',source_url=ev.source_url,contact_status='Published partner route',route=route,reason_to_contact='Clarify account ownership',next_ask=ask,authority_boundary='Partner contact; not government decision maker',evidence=(ev,)),)
        overlays.append(ReviewedSubject(subject_id=subject.subject_id,source_sha256=subject.source_sha256,research=research,targets=targets))
    book=ReviewedCases(schema_version='reviewed_cases.v2',client_name='NETSCOUT',subjects=overlays)
    updated,d,index=build_assess_run('NETSCOUT',sweep,profile,BINDING,as_of=NOW,reviewed_cases=book,projection_inputs={'reviewed_cases_sha256':_digest(book.model_dump(mode='json'))})
    assert updated.run_id!=run.run_id
    assert {s.subject_id for s in updated.research.items}=={s.subject_id for s in run.research.items}
    assert [s.evidence for s in updated.research.items]==[s.evidence for s in run.research.items]
    receipt=run_press(assess=updated,reviewed_cases=book)
    assert check_reviewed_quality(receipt,book)['passed']
    html=render_html(receipt)
    assert 'Bill Lytle' in html and 'not government decision maker' in html and 'https://www.techanax.com/nasa-sewp-v' in html
    assert 'SIRC organization route info@sirc.net' in html
    assert all(o.research.next_ask in html for o in overlays)
    modified=book.model_copy(update={'subjects':(overlays[0].model_copy(update={'source_sha256':'a'*64}),)})
    with pytest.raises(ValueError):run_press(assess=updated,reviewed_cases=modified)


def test_award_detail_limits_failures_and_invalid_identity_are_visible(monkeypatch):
    from types import SimpleNamespace
    raw=AWARDS[0]['data'];sid=raw['generated_unique_award_id']
    record={'kind':'award','award_id':raw['piid'],'generated_internal_id':sid,'description':raw['description'],'url':f'https://www.usaspending.gov/award/{sid}'}
    buyer={'buyers':[{'agency':'Department of Homeland Security','buyer':'CBP','records':[record,{**record,'generated_internal_id':'wrong'}]}]}
    profile=SimpleNamespace(client_name='NETSCOUT')
    limited=enrich_buyer_research(buyer,profile,[],fetch=lambda u:raw,limit=0)
    receipt=limited['research_award_details']
    assert receipt['selected_rows']==2 and receipt['detail_requests']==0
    assert receipt['counts']['invalid']==1 and receipt['counts']['not_run']==1
    invalid=next(a for a in receipt['attempts'] if a['status']=='invalid')
    assert invalid['source_record_id']=='wrong' and invalid['supplied_award_id']==raw['piid']
    assert limited['buyers']==buyer['buyers']
    bad=enrich_buyer_research(buyer,profile,[],fetch=lambda u:AWARDS[1]['data'])['research_award_details']
    assert bad['counts']['failed']==1 and bad['detail_requests']==1 and not bad['records']
    monkeypatch.setenv('LILA_SUITE_OFFLINE','1')
    offline=enrich_buyer_research(buyer,profile,[])['research_award_details']
    assert offline['counts']['not_run']==1 and offline['detail_requests']==0


def test_raw_apfs_fields_migration_keeps_store_identity_without_market_event(monkeypatch,tmp_path):
    from tools.api.forecasts.store import upsert
    monkeypatch.setenv('LILA_FORECAST_STORE_DIR',str(tmp_path))
    current=rows()[0];old=current.model_copy(update={'source_fields':{}})
    initial,events=upsert('dhs_apfs',[old],today='2026-09-12')
    revised,events=upsert('dhs_apfs',[current],today='2026-09-13')
    assert not events
    assert list(initial)==list(revised)==[current.source_id]
    assert revised[current.source_id]['first_seen']=='2026-09-12'
    assert revised[current.source_id]['source_fields']
