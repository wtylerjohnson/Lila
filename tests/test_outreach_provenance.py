"""Offline native manual contact provenance; no outbound action or graph writes."""
import pytest
from tools.contact_graph.outreach import OutreachList
from tools.contact_graph.store import ContactGraphStore
import ui.server as server

@pytest.fixture
def rail_api(tmp_path,monkeypatch):
    store=ContactGraphStore(root=tmp_path/'contacts')
    rail=OutreachList(store=store)
    monkeypatch.setattr(server,'_outreach',lambda:rail)
    return rail,server.app.test_client(),tmp_path

def test_manual_partner_source_survives_first_add_readd_and_disk(rail_api):
    rail,api,root=rail_api
    payload={'person_name':'Bill Lytle','agency':'TechAnax','title':'SEWP Program Manager',
             'source_url':'https://www.techanax.com/nasa-sewp-v',
             'source_note':'Published partner route; account owner unknown',
             'authority_boundary':'Company partner; not government buyer'}
    first=api.post('/api/outreach/add',json=payload)
    assert first.status_code==200
    for key in ('source_url','source_note','authority_boundary','title','agency'):
        assert first.get_json()[key]==payload[key]
    for extras in ({},{'source_url':'','source_note':None,'authority_boundary':''},
                   {'source_url':'https://example.com/','source_note':'replacement','authority_boundary':'replacement'}):
        repeated=api.post('/api/outreach/add',json={'person_name':'Bill Lytle','agency':'TechAnax',**extras})
        assert repeated.status_code==200
        for key in ('source_url','source_note','authority_boundary'):
            assert repeated.get_json()[key]==payload[key]
    saved=OutreachList(store=ContactGraphStore(root=root/'contacts')).load()['entries']
    assert len(saved)==1 and saved[0]['authority_boundary']==payload['authority_boundary']
    assert not list(root.rglob('observations.jsonl'))

def test_readd_can_fill_missing_provenance_without_promoting_role(rail_api):
    rail,api,_=rail_api
    rail.add_manual('Bill Lytle','TechAnax',title='Partner program manager')
    result=api.post('/api/outreach/add',json={'person_name':'Bill Lytle','agency':'TechAnax',
        'source_url':'https://www.techanax.com/','source_note':'Published company route',
        'authority_boundary':'Not a government official'}).get_json()
    assert result['source_note']=='Published company route'
    assert result['title']=='Partner program manager'

@pytest.mark.parametrize('field,value',[('source_note',{}),('authority_boundary',123),
    ('source_url','javascript:alert(1)'),('source_url','https://user:pass@example.com/'),('source_url','not a URL')])
def test_invalid_manual_provenance_refused_before_write(rail_api,field,value):
    rail,api,_=rail_api
    result=api.post('/api/outreach/add',json={'person_name':'Bill Lytle',field:value})
    assert result.status_code==400
    assert rail.load()['entries']==[]
    with pytest.raises(ValueError):rail.add_manual('Bill Lytle',None,**{field:value})
