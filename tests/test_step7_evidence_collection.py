"""Step7: acquired bytes and locators are auditable research, not approval."""
import hashlib
import json

import httpx
import pytest

from tools.api import _http, sam_attachment_text as sat, sam_notice_detail as detail
from tools.api.sam_capture import read_retained

# Capture the real helper before the autouse live-network observer wraps it.
# Every use below binds httpx.MockTransport; no socket or source call occurs.
_NATIVE_REQUEST = _http._request


def public_file(resource_id='b'*32, name='SOW.txt'):
    return {'resource_id': resource_id, 'name': name, 'access_level': 'public',
            'access_status': 'public', 'export_controlled': '0', 'explicit_access': '0'}


def http_fixture(monkeypatch, payload, status=200):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, content=payload)
    monkeypatch.setattr(_http, '_client', lambda timeout: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(_http, '_request', _NATIVE_REQUEST)
    return calls


@pytest.mark.parametrize('payload,status,expected', [
    (b'{ "attachments" : [] }',200,'confirmed_empty'),
    (b'{"error":"unknown"}',200,'unrecognized_inventory'),
    (b'{"attachments":[{"name":"x.txt","resourceId":"' + b'b'*32 + b'"}],"opportunityId":"' + b'c'*32 + b'"}',200,'identity_mismatch'),
    (b'access denied',403,'lookup_failed'),
])
def test_inventory_retains_exact_response_and_distinct_state(tmp_path, monkeypatch, payload,status,expected):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR',str(tmp_path))
    http_fixture(monkeypatch,payload,status)
    result=detail.fetch_notice_resources('a'*32)
    assert result['collection_status']==expected
    assert read_retained(tmp_path,result['raw_capture'])==payload
    assert result['raw_capture']['http_status']==status
    assert result['raw_capture']['complete'] is True
    assert result['resources_checked'] is (expected=='confirmed_empty')


def test_inventory_cache_rebinds_raw_projection_and_changed_response(tmp_path,monkeypatch):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR',str(tmp_path))
    calls=http_fixture(monkeypatch,b'{"attachments":[]}')
    first=detail.fetch_notice_resources('a'*32)
    assert detail.fetch_notice_resources('a'*32)['from_cache'] is True
    assert len(calls)==1
    cache=tmp_path/('a'*32+'.resources.json');saved=json.loads(cache.read_text())
    saved['attachments']=[{'name':'Invented SOW'}];cache.write_text(json.dumps(saved))
    current={'attachments':[{'name':'New SOW.txt','resourceId':'d'*32}]}
    http_fixture(monkeypatch,json.dumps(current).encode())
    refreshed=detail.fetch_notice_resources('a'*32)
    assert refreshed['from_cache'] is False
    assert refreshed['attachments'][0]['name']=='New SOW.txt'
    assert read_retained(tmp_path,first['raw_capture'])==b'{"attachments":[]}'


def test_http_capture_cap_retains_partial_not_complete(monkeypatch):
    http_fixture(monkeypatch,b'0123456789')
    observed=[]
    with pytest.raises(ValueError,match='byte boundary'):
        _http.get_json('https://sam.gov/example',retries=1,max_response_bytes=4,
                       response_observer=lambda response,raw,complete:observed.append((raw,complete)))
    assert observed==[(b'0123',False)]


def test_parse_failure_keeps_acquired_original_bytes(tmp_path,monkeypatch):
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR',str(tmp_path))
    monkeypatch.setattr(sat,'_download_bytes',lambda *a,**k:b'broken PDF original')
    monkeypatch.setattr(sat,'_text_from_bytes',lambda *a,**k:(_ for _ in ()).throw(ValueError('unreadable pdf')))
    result=sat.extract_public_attachment_text(public_file(name='SOW.pdf'))
    assert result['collection_status']=='unreadable' and not result['text']
    assert result['retrieved_at']
    assert read_retained(tmp_path,result['raw_capture'])==b'broken PDF original'
    assert result['sha256']==hashlib.sha256(b'broken PDF original').hexdigest()


@pytest.mark.parametrize('tamper',['text','raw','identity','metadata','json'])
def test_text_cache_cannot_reuse_unbound_evidence(tmp_path,monkeypatch,tamper):
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR',str(tmp_path))
    monkeypatch.setattr(sat,'_download_bytes',lambda *a,**k:b'Original requirement')
    attachment=public_file();first=sat.extract_public_attachment_text(attachment)
    cache=tmp_path/('b'*32+'.json');saved=json.loads(cache.read_text())
    if tamper=='text':saved['text']='Invented requirement'
    if tamper=='identity':saved['resource_id']='e'*32
    if tamper=='raw':(tmp_path/saved['raw_capture']['path']).write_bytes(b'changed')
    if tamper=='metadata':attachment['name']='Changed SOW.txt'
    if tamper=='json':saved=[]
    cache.write_text(json.dumps(saved))
    monkeypatch.setattr(sat,'_download_bytes',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('source unavailable')))
    result=sat.extract_public_attachment_text(attachment)
    assert result['collection_status']=='lookup_failed' and not result['text']
    assert result['from_cache'] is False


def native_collection(tmp_path,monkeypatch, notice_id="a"*32, term="network visibility"):
    import run_searches as searches
    from agents.schemas import RawOpportunity
    from tools.relevance.taxonomy import CapabilityTaxonomy,TaxonomyTerm
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR',str(tmp_path))
    monkeypatch.setattr('tools.api.sam_quota.calls_today',lambda:0)
    files=[public_file('b'*32,'SOW 1.txt'),public_file('c'*32,'SOW 2.txt'),
           {**public_file('d'*32,'SOW 3.pdf'),'access_level':'private'},
           public_file('e'*32,'Other.xlsx')]
    inventory=detail._manifest_hash(files)
    monkeypatch.setattr(detail,'fetch_notice_resources',lambda *a,**k:{'id':notice_id,'resources_checked':True,'attachments':files,'attachment_inventory_hash':inventory})
    texts={'b'*32:f'Buyer requires {term}. Café data.','c'*32:f'Further {term} deliverables: Δ.'}
    monkeypatch.setattr(sat,'_download_bytes',lambda rid,**k:texts[rid].encode())
    candidate=RawOpportunity(source='sam.gov',source_id=notice_id,title='Requirement',agency='NASA',raw_payload={'type':'Solicitation'})
    class Source:
        last_attachment_census={'selected':1}
        def attachment_candidates(self,*a,**k):return [candidate]
    rows,stats=searches._enrich_sam_public_attachments([],Source(),object(),CapabilityTaxonomy(client_name='Testco',version=1,updated='2026-07-21',core=[TaxonomyTerm(term=term)]),None,limit=8)
    return rows[0],stats,inventory,texts


def test_native_unicode_file_locators_and_selected_inventory_states_reach_dossier(tmp_path,monkeypatch):
    from agents.decisions.dossier import _attachment_research_context
    row,stats,inventory,texts=native_collection(tmp_path,monkeypatch)
    raw=row['raw_payload'];context=_attachment_research_context(row,{'id':'a'*32,'resources_checked':True,'attachment_inventory_hash':inventory})
    assert context['status']=='discovery_only'
    assert len(context['file_passages'])==2
    for item in context['file_passages']:
        assert item['text']==texts[item['resource_id']]
        start,end=item['locator']['start'],item['locator']['end']
        assert raw['text'][start:end]==item['text']
        assert item['authority']=='discovery_only'
    file_states={r['resource_id']:r['status'] for r in stats['attachment_record_receipts']['a'*32]['files']}
    assert file_states=={'b'*32:'captured_discovery_only','c'*32:'captured_discovery_only','d'*32:'inaccessible','e'*32:'not_fetched'}
    assert 'source_depth' not in raw and 'full_description' not in raw


@pytest.mark.parametrize('tamper',['offset','notice','authority','inventory'])
def test_dossier_rejects_changed_locator_receipt_even_if_rehashed(tmp_path,monkeypatch,tamper):
    from agents.decisions.dossier import _attachment_research_context
    row,stats,inventory,texts=native_collection(tmp_path,monkeypatch)
    raw=row['raw_payload'];receipt=raw['attachment_collection_v1']
    if tamper=='offset':receipt['files'][0]['combined_text_locator']['end']+=1
    if tamper=='notice':receipt['notice_id']='f'*32
    if tamper=='authority':receipt['authority']='trusted_requirement'
    if tamper=='inventory':receipt['inventory_hash']='0'*64
    raw['attachment_collection_sha256']=hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert _attachment_research_context(row,{'id':'a'*32,'attachment_inventory_hash':inventory})['status']=='unavailable'


@pytest.mark.parametrize('copied_review', [False, True])
def test_collected_attachment_text_cannot_supply_strict_notice_requirement(tmp_path,monkeypatch,copied_review):
    from agents.assess.contracts import LiveClassification
    from agents.assess.ledger import build_assess_run
    from tests.test_assess_ledger import _sweep,_build,_depth_record,_profile,BINDING,NOW
    row,stats,inventory,texts=native_collection(tmp_path,monkeypatch,notice_id='N1',term='packet capture')
    sweep=_sweep();original=sweep['results']['sam.gov'][0]
    original['source_id']=row['source_id']
    original['raw_payload'].update(row['raw_payload'])
    original['title']='Lawn maintenance'
    original['raw_payload']['description_snippet']='Lawn maintenance'
    depth=_depth_record()
    depth['source_depth']['description']='The contractor shall provide lawn maintenance.'
    sweep['results']['dossiers']={'depth_records':[depth]}
    run,diagnostics,index=_build(sweep)
    parent=next(r for r in run.live.records if r.notice_id==row['source_id'])
    if copied_review:
        reviews={'schema_version':1,'client':'Testco','binding':dict(BINDING),'reviews':[{
            'notice_id':'N1','evidence_id':parent.authoritative_evidence[-1].evidence_id,
            'excerpt':row['raw_payload']['text'].split('\n\n')[0],
            'capability_terms':['packet capture'],'decision':'approved',
            'attachment_inventory_count':parent.attachment_inventory_count,
            'attachment_inventory_hash':parent.attachment_inventory_hash,
            'attachments_reviewed':True,'reviewed_by':'operator','reviewed_at':NOW.isoformat()}]}
        run,_,_=build_assess_run('Testco',sweep,_profile(),BINDING,
            requirement_reviews_payload=reviews,as_of=NOW)
        parent=next(r for r in run.live.records if r.notice_id=='N1')
    assert parent.classification!=LiveClassification.BID_NOW
    assert parent.requirement_reviewed_at is None
    assert not parent.requirement_excerpt
    assert parent.attachment_gap
    assert 'Café data' not in parent.model_dump_json()


def test_collected_attachment_context_preserves_independently_supported_bid_now(tmp_path,monkeypatch):
    import copy
    from agents.assess.contracts import LiveClassification
    from tests.test_assess_ledger import _sweep,_depth_record,_build_with_requirement_review
    row,_,_,_=native_collection(tmp_path,monkeypatch,notice_id='N1',term='packet capture')
    plain=_sweep();plain['results']['dossiers']={'depth_records':[_depth_record()]}
    enriched=copy.deepcopy(plain)
    enriched['results']['sam.gov'][0]['raw_payload'].update(row['raw_payload'])
    records=[]
    for sweep in [plain,enriched]:
        run,_,_=_build_with_requirement_review(sweep)
        records.append(next(r for r in run.live.records if r.notice_id=='N1'))
    assert all(r.classification==LiveClassification.BID_NOW for r in records)
    assert records[0].requirement_excerpt==records[1].requirement_excerpt
    assert records[0].recommendation==records[1].recommendation
    assert 'Café data' not in records[1].model_dump_json()


def test_invalid_resource_is_identity_gap_without_network(monkeypatch):
    monkeypatch.setattr(sat,'_download_bytes',lambda *a,**k:pytest.fail('unexpected network'))
    result=sat.extract_public_attachment_text(public_file('../wrong'))
    assert result['collection_status']=='identity_mismatch'


def test_download_access_denial_is_distinct_from_unreadable(tmp_path,monkeypatch):
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR',str(tmp_path))
    response=httpx.Response(403,request=httpx.Request('GET','https://sam.gov/example'))
    def denied(*a,**k):response.raise_for_status()
    monkeypatch.setattr(sat,'_download_bytes',denied)
    result=sat.extract_public_attachment_text(public_file())
    assert result['collection_status']=='inaccessible'
    assert 'raw_capture' not in result
