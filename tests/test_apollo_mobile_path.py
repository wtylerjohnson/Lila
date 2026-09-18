"""Returned provider channels survive to native targets; no live calls."""
import json
import pytest

from agents.golden_press import phone_policy, targets_store
from agents.assess import investigation_inputs as inputs, investigations as worker
from agents.assess.research_subjects import make_subject
from agents.leadgen.research_event import contact_gaps
from agents.leadgen.target_html import render_targets
from tests.test_source_conversation import NOW, PROFILE, row, sweep, draft
from tools import apollo_targets


def person():
    return {'id':'provider-1','name':'Jordan Example','title':'Cloud Architect',
        'email':'jordan@example.gov','email_status':'verified',
        'organization':{'name':'Missile Defense Agency','phone':'+12025550100'},
        'mobile_phone':[{'sanitized_number':'+12025550101','status_cd':'valid_number',
                         'dnc_status_cd':'not_found','confidence_cd':'high'}]}


def saved(monkeypatch, tmp_path, record=None):
    monkeypatch.setenv('LILA_TARGETS_STORE_DIR',str(tmp_path/'targets'))
    monkeypatch.setenv('LILA_INVESTIGATION_SUPPLEMENTS_PATH',str(tmp_path/'absent.json'))
    monkeypatch.setenv('LILA_INVESTIGATION_NOTICE_STORE_PATH',str(tmp_path/'absent.db'))
    from tools.contact_graph.store import ContactGraphStore
    monkeypatch.setattr(ContactGraphStore,'read_observations',lambda self:[])
    monkeypatch.setattr(apollo_targets,'_utc_now',lambda:NOW)
    monkeypatch.setattr(apollo_targets,'_http_post',lambda *a,**k:pytest.fail('Unexpected network call'))
    monkeypatch.setattr('agents.golden_press.person_screen.screen_person',lambda *a,**k:{'state':'accepted','findings':[],'cautions':[],'verdict':'pass'})
    base={'contact_id':'provider-1','name':'Jordan Example','spec_id':'spec','join_record_ids':['one'],
          'title':'Cloud Architect','organization':'Missile Defense Agency',
          'provenance':{'class':'apollo','retrieved_at':NOW.date().isoformat(),'source':'apollo.people_bulk_match'}}
    enriched=apollo_targets.enrich([base],key='fixture',matcher=lambda payload:{'matches':[record or person()]})['rows'][0]
    targets_store.write('veeam',{'receipt':{'attempted':True},'contacts':[enriched]})
    return enriched


def test_enrichment_store_capture_bind_and_render_keeps_verified_mobile(tmp_path,monkeypatch):
    enriched=saved(monkeypatch,tmp_path)
    subject=make_subject('program',row())
    data=sweep(row());data['results']['upstream_investigation_inputs']=inputs.capture([subject],PROFILE,NOW)
    bundle=worker.bundle_for(subject,data,PROFILE,NOW)
    key=next(iter(bundle['contacts']))
    result=worker.bind_draft(subject,bundle,worker.InvestigationDraft.model_validate(draft(bundle,
        contacts=[{'contact_id':key,'reason_to_contact':'Locate the recovery owner','first_question':'Who owns recovery acceptance?'}])),NOW)
    target=result.targets[0]
    assert target.phone=='+12025550101' and target.email=='jordan@example.gov'
    assert not contact_gaps(target,NOW)
    assert target.channel_verification.dnc_status=='not_found'
    assert target.channel_verification.verified_at==NOW
    html=render_targets(result.targets)
    assert '+12025550101' in html and '<dd>mobile</dd>' in html
    assert '+12025550100' not in html
    assert all(not e.primary_source for e in target.evidence)
    assert 'buying authority unconfirmed' in target.authority_boundary
    parsed=json.loads(next(e.excerpt for e in target.evidence if e.evidence_id==target.channel_verification.evidence_id))
    assert parsed['provider_phone']['confidence_cd']=='high'


@pytest.mark.parametrize('shape',['phone_numbers','mobile_phone'])
def test_provider_shapes_retain_status_dnc_and_observation(shape):
    num={'sanitized_number':'+12025550101','type':'mobile','status':'valid_number','dnc_status_cd':'pending'}
    got=phone_policy.from_enrichment({shape:[num]},observed_at=NOW.isoformat())[0]
    assert got['number']==num['sanitized_number'] and got['type']=='mobile'
    assert got['dnc_status_cd']=='pending' and got['status_cd']=='valid_number'
    assert got['observed_at']==NOW.isoformat()


@pytest.mark.parametrize('change',[{'dnc_status_cd':'found'},{'status_cd':'invalid'}])
def test_blocked_number_retained_in_store_but_not_person_channel(tmp_path,monkeypatch,change):
    p=person();p['mobile_phone'][0].update(change);saved(monkeypatch,tmp_path,p)
    rows,_=targets_store.admissible(targets_store.load('veeam'))
    assert rows[0]['phones'][0].items()>=change.items()
    values,withheld=inputs._apollo_channels(rows[0],NOW)
    assert values['phone'] is None and withheld==1
    assert phone_policy.select(rows[0]['phones'],phone_policy.MOBILE) is None
    assert targets_store.phone_of(rows[0],phone_policy.MOBILE) is None


@pytest.mark.parametrize('kind',['home_phone','other_phone','corporate_phone','phone_number'])
def test_other_phone_types_never_substitute_for_mobile(kind):
    p={kind:[{'sanitized_number':'+12025550101','status_cd':'valid_number'}]}
    phones=phone_policy.from_enrichment(p)
    values,_=inputs._apollo_channels({'name':'Example','phones':phones},NOW)
    assert values['phone'] is None


def test_duplicate_number_cannot_hide_a_dnc_finding():
    p=person();p['phone_numbers']=[{'sanitized_number':'+12025550101','type':'mobile','dnc_status_cd':'found'}]
    entries=phone_policy.from_enrichment(p)
    assert entries[0]['dnc_status_cd']=='found' and not phone_policy.renders(entries[0])


def test_bare_duplicate_does_not_erase_explicit_mobile_type():
    p=person();p['phone_number']='+12025550101'
    entry=phone_policy.select(phone_policy.from_enrichment(p),phone_policy.MOBILE)
    assert entry['number']=='+12025550101' and entry['status_cd']=='valid_number'


def test_email_refresh_does_not_drop_saved_mobile_or_refresh_its_timestamp(monkeypatch):
    stamp='2026-08-01T10:00:00+00:00'
    old=phone_policy.from_enrichment(person(),observed_at=stamp)
    monkeypatch.setattr(apollo_targets,'_utc_now',lambda:NOW)
    result=apollo_targets.enrich([{'contact_id':'provider-1','phones':old}],key='fixture',
        matcher=lambda _: {'matches':[{'id':'provider-1','email':'jordan@example.gov','email_status':'verified'}]})
    phone=phone_policy.select(result['rows'][0]['phones'],phone_policy.MOBILE)
    assert phone['observed_at']==stamp and phone['status_cd']=='valid_number'


def test_new_email_observation_does_not_refresh_an_old_mobile():
    old='2024-06-01T10:00:00+00:00'
    values,_=inputs._apollo_channels({'name':'Example','email':'example@example.gov','email_status':'verified',
        'phones':phone_policy.from_enrichment(person(),observed_at=old),
        'provenance':{'retrieved_at':'2026-09-17'}},NOW)
    assert values['verified_at']==old


@pytest.mark.parametrize('stamp',['2026-09-18T00:00:00Z','2026-09-18','not-a-date','2026-09-16T12:00:00'])
def test_future_or_invalid_observation_cannot_reach_current_capture(stamp):
    phones=phone_policy.from_enrichment(person(),observed_at=stamp)
    got,_=inputs._apollo_channels({'name':'Example','phones':phones},NOW)
    assert got['phone'] is None


def test_legacy_typed_mobile_does_not_invent_valid_status_or_refresh_date():
    values,_=inputs._apollo_channels({'name':'Example','email':'email_not_unlocked@example.com','email_status':'verified',
        'phones':[{'number':'+12025550101','type':'mobile','basis':'provider type'}],
        'provenance':{'retrieved_at':'2024-06-01'}},NOW)
    assert values['phone']=='+12025550101' and values['email'] is None
    assert values['phone_status']=='unknown' and values['dnc_status']=='not_supplied'
    assert values['verified_on']=='2024-06-01'


def test_explicit_phone_suppression_still_controls_research_capture(monkeypatch):
    monkeypatch.setenv(phone_policy.INCLUDE_MOBILE_ENV,'off')
    got,_=inputs._apollo_channels({'name':'Example','phones':phone_policy.from_enrichment(person())},NOW)
    assert got['phone'] is None


def test_search_only_identity_keeps_returned_phone_status():
    p=person();p['phone_numbers']=[{'sanitized_number':'+12025550102','type':'direct','status_cd':'valid_number'}]
    spec={'spec_id':'spec','target_org':{'name':'Missile Defense Agency'}}
    got=apollo_targets._identity(p,spec,retrieved_at=NOW.date().isoformat())
    assert got['email_status']=='verified'
    assert phone_policy.select(got['phones'],phone_policy.MOBILE)['status_cd']=='valid_number'
    channels,_=inputs._apollo_channels(got,NOW)
    assert channels['phone']=='+12025550101'
    assert channels['verified_on']==NOW.date().isoformat()
    assert 'verified_at' not in channels
