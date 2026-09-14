import json
from pathlib import Path
from datetime import datetime, date, timezone

RAW=json.loads((Path(__file__).parent/'fixtures/apfs_pocs_74247.json').read_text())
NOW=datetime(2026,9,14,tzinfo=timezone.utc)

def mapped():
    from tools.api.forecasts.dhs_apfs import map_record
    return map_record(RAW,retrieved_at=NOW)

def test_primary_alternate_and_coordinator_are_structured():
    r=mapped()
    assert [c.name for c in r.contacts]==['Casey Cauffman','Shauntynee Penix','Kimberly Witcher']
    assert [c.contact_type for c in r.contacts]==['primary','alternate','small_business_coordinator']
    assert all(c.email and c.phone for c in r.contacts)
    assert r.contact_publication_date==date(2026,9,11)
    assert r.source_fields==RAW

def test_inline_forecast_harvest_is_idempotent_and_keeps_roles(tmp_path):
    from tools.contact_graph.harvest import harvest_from_results
    from tools.contact_graph.store import ContactGraphStore
    from tools.api.forecasts.store import record_payload
    row=record_payload(mapped());store=ContactGraphStore(tmp_path)
    data={'forecast_signals':{'matched':[row],'research_candidates':[{'record':row}]}}
    a=harvest_from_results(data,store=store,harvested_at=NOW,force=True)
    assert a.observations_written==6
    assert harvest_from_results(data,store=store,harvested_at=NOW,force=True).observations_written==0
    obs=store.read_observations()
    assert {x.role_type for x in obs}=={'primary','alternate','small_business_coordinator'}
    assert all(x.source=='dhs_apfs' and x.notice_type=='Forecast' for x in obs)
    assert all(x.observed_at==date(2026,9,11) for x in obs)
    assert all(x.source_url.endswith('/74247/public-print/') and x.api_ref is None for x in obs)

def test_saved_source_pocs_visible_outside_source_drawer():
    from tools.api.forecasts.store import record_payload
    from agents.assess.research_subjects import make_subject
    from agents.leadgen.research_html import render_forecast_contacts
    row=record_payload(mapped());row.pop('contacts',None);row.pop('contact_publication_date',None)
    subject=make_subject('forecast',row,as_of=NOW)
    html=render_forecast_contacts(subject)
    assert all(x in html for x in ['Casey Cauffman','Shauntynee Penix','Kimberly Witcher','(202) 836-3519'])
    assert '<details' not in html and 'mailto:casey.cauffman@usss.dhs.gov' in html
    assert 'Published phone' in html and 'Mobile' not in html
    assert json.loads(subject.source_payload_json)==row
    from agents.reports.lint import lint_contact_rendering
    assert lint_contact_rendering(html).ok
    assert 'data-grade="C"' in html
    fresh=render_forecast_contacts(subject,as_of=NOW.date())
    assert 'data-grade="A"' in fresh and lint_contact_rendering(fresh).ok
    future=render_forecast_contacts(subject,as_of=date(2026,9,1))
    assert 'data-grade="C"' in future


def test_no_publication_date_is_invented_from_harvest():
    from tools.api.forecasts.dhs_apfs import map_record
    from tools.contact_graph.forecasts import observations_from_forecast
    for changes in [{'publish_date':None,'published_date':None},
                    {'published_date':'08/11/2026'},
                    {'publish_date':'not-a-date'},
                    {'publish_date':'09/30/2026','published_date':'09/30/2026'}]:
        row=map_record(RAW|changes,retrieved_at=NOW)
        assert all(o.observed_at is None for o in observations_from_forecast(row,harvested_at=NOW))


def test_missing_channels_and_empty_contacts_are_retained_honestly():
    from tools.api.forecasts.dhs_apfs import map_record
    from tools.contact_graph.forecasts import observations_from_forecast
    changes={k:None for k in RAW if k.startswith(('requirements_contact_', 'alternate_contact_', 'sbs_coordinator_'))}
    empty=map_record(RAW|changes,retrieved_at=NOW)
    assert empty.contacts==[] and observations_from_forecast(empty,harvested_at=NOW)==[]
    named=map_record(RAW|changes|{'requirements_contact_first_name':'Casey','requirements_contact_last_name':'Cauffman'},retrieved_at=NOW)
    obs=observations_from_forecast(named,harvested_at=NOW)
    assert len(obs)==1 and obs[0].person_name=='Casey Cauffman' and obs[0].channel_kind is None


def test_forecast_contact_identity_cannot_borrow_another_source():
    from tools.api.forecasts.contacts import contact_record
    row=mapped().model_dump(mode='json')
    for patch in [{'url':'https://apfs-cloud.dhs.gov/record/123/public-print/'},
                  {'url':'https://example.com/record/74247/public-print/'},
                  {'source_id':'F9999999999'}]:
        assert contact_record(row|patch) is None
    # APFS uses original fields even if a derived contact list was tampered with.
    row['contacts']=[{'name':'Injected','email':'injected@example.com'}]
    assert contact_record(row)['contacts'][0].name=='Casey Cauffman'


def test_existing_ledger_rejects_tampered_new_contact_fields():
    import pytest
    from agents.assess.research_subjects import make_subject
    row=mapped().model_dump(mode='json');row['contacts'][0]['email']='wrong@example.com'
    with pytest.raises(ValueError,match='contacts'):
        make_subject('forecast',row,as_of=NOW)


def test_same_person_two_published_roles_survive_graph_dedup(tmp_path):
    from tools.api.forecasts.dhs_apfs import map_record
    from tools.contact_graph.forecasts import observations_from_forecast
    from tools.contact_graph.store import ContactGraphStore
    raw=RAW.copy()
    for suffix in ['first_name','last_name','email','phone']:
        raw['alternate_contact_'+suffix]=raw['requirements_contact_'+suffix]
    obs=observations_from_forecast(map_record(raw,retrieved_at=NOW),harvested_at=NOW)
    store=ContactGraphStore(tmp_path);assert store.append(obs).written==6
    assert store.append(obs).written==0
    assert {o.role_type for o in store.read_observations() if o.person_name=='Casey Cauffman'}=={'primary','alternate'}


def test_other_forecast_source_uses_own_identity_and_typed_contacts():
    from agents.schemas import ForecastRecord, OpportunityContact
    from tools.contact_graph.forecasts import observations_from_forecast
    record=ForecastRecord(source='other_forecast',source_id='OTHER-1',agency='Other agency',title='Other client',
        url='https://example.gov/forecasts/1',retrieved_at=NOW,contact_publication_date=date(2026,9,1),
        contacts=[OpportunityContact(name='Alex Example',email='alex@example.gov',contact_type='primary')])
    obs=observations_from_forecast(record,harvested_at=NOW)
    assert len(obs)==1 and obs[0].source=='other_forecast'
    assert obs[0].observed_at==date(2026,9,1) and obs[0].api_ref is None


def test_native_assess_to_report_keeps_forecast_pocs_without_lead_promotion(monkeypatch,tmp_path):
    from tests.test_netscout_forecast_research import ordinary_forecast_producer
    from tests.test_assess_ledger import _sweep,_profile,BINDING
    from tools.relevance.taxonomy import CapabilityTaxonomy,TaxonomyTerm
    from agents.assess.ledger import build_assess_run,persist_assess_run
    from agents.leadgen.export_assess import export_current_assess_run
    from agents.leadgen.press import run_press,render_html,render_markdown
    tax=CapabilityTaxonomy(client_name='NETSCOUT',version=1,updated='2026-09-14',core=[TaxonomyTerm(term='Wi-Fi')])
    payload,_=ordinary_forecast_producer(monkeypatch,[mapped()],taxonomy=tax)
    assert payload['research_candidates']
    sweep=_sweep();sweep.update(client='NETSCOUT',generated_at=NOW.isoformat())
    sweep['results']['forecast_signals']=payload
    profile=_profile().model_copy(update={'client_name':'NETSCOUT'})
    run,diag,index=build_assess_run('NETSCOUT',sweep,profile,BINDING,as_of=NOW)
    persist_assess_run(run,BINDING,diag,index,state_dir=tmp_path)
    receipt=run_press(assess=export_current_assess_run('NETSCOUT',scope='all',state_dir=tmp_path),client_name='NETSCOUT')
    parent=next(p for p in receipt.parents if p.research_subject and '74247' in p.research_subject.source_record_id)
    assert not parent.lead_ids
    html=render_html(receipt);md=render_markdown(receipt)
    for name in ['Casey Cauffman','Shauntynee Penix','Kimberly Witcher']:
        assert name in html and name in md
    assert html.index('Published forecast contacts') < html.index('Show work: original source fields')


def test_market_map_forecast_card_renders_contacts_before_broader_targets():
    from agents.golden_press.records import GoldenRecord
    from agents.golden_press.external_product_projection import _graph_record
    from agents.golden_press.external_product_render import _record
    rec=mapped()
    golden=GoldenRecord(record_id=rec.source_id,lane='L4_forecast',title=rec.title,agency=rec.agency,
        url=rec.url,source_fields=rec.source_fields,forecast_source=rec.source,
        contacts=[c.model_dump(mode='json') for c in rec.contacts])
    projected=_graph_record(golden.model_dump(mode='json'),kind='forecast')
    projected['targets']=[{'name':'Broader target','role':'Agency leader'}]
    html=_record(projected,include_targets=True)
    assert html.index('Kimberly Witcher') < html.index('Broader target')
    assert 'Published phone' in html and '(917) 843-3204' in html


def test_new_publication_appends_new_observation_without_rewriting_old(tmp_path):
    from tools.api.forecasts.dhs_apfs import map_record
    from tools.contact_graph.forecasts import observations_from_forecast
    from tools.contact_graph.store import ContactGraphStore
    store=ContactGraphStore(tmp_path)
    assert store.append(observations_from_forecast(mapped(),harvested_at=NOW)).written==6
    updated=map_record(RAW|{'publish_date':'09/14/2026','published_date':'09/14/2026'},retrieved_at=NOW)
    obs=observations_from_forecast(updated,harvested_at=NOW)
    assert store.append(obs).written==6 and store.append(obs).written==0
    assert {o.observed_at for o in store.read_observations()}=={date(2026,9,11),date(2026,9,14)}
