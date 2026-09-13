"""Step3 prespecified synthetic pairs and independently found boundary cases."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from tools.relevance.taxonomy import load_taxonomy
from tools.relevance.requirement_support import requirement_support
from tools.api.sam_extract import _to_opportunity, _screen_evidence_with_coverage
from tools.api.sam_gov import map_notice
from agents.decisions.triage import deterministic_prefilter, triage_notices, TriageResult, TriageItem

TAX = load_taxonomy('apexanalytix')
CASES = json.loads((Path(__file__).parent/'fixtures/step3/prespecified_cases.json').read_text())['cases']


def notice(text, **extra):
    return dict(source_id='N', title='Platform procurement', description=text, naics_code='541512', **extra)


def extract(text, title='Platform procurement'):
    row = json.loads(_to_opportunity({'notice_id': 'N', 'title': title, 'description': text, 'naics': '541512'}).model_dump_json())
    evidence, coverage = _screen_evidence_with_coverage(title, text, ['supplier risk management', 'vendor risk management'])
    for context in evidence:
        context['source_id'] = 'N'
    row['raw_payload'].update(screen_evidence_matches=evidence, screen_evidence_coverage=coverage)
    return row


@pytest.mark.parametrize('case', CASES, ids=lambda c:c['id'])
def test_prespecified_pairs(case):
    row = notice(case['description'], notice_type=case.get('notice_type', 'Solicitation'))
    row['title'] = case.get('title', row['title'])
    before = copy.deepcopy(row)
    support = requirement_support(row, TAX)
    expected = None if case['expected_requested_support'] == 'unresolved' else case['expected_requested_support']
    assert support['requested_support'] is expected
    candidates, ruled, _ = deterministic_prefilter([row], TAX)
    assert bool(candidates) is (expected is True)
    if expected is None:
        assert ruled['N']['verdict'] == 'unscreened'
    assert row == before


@pytest.mark.parametrize('text,expected', [
    ('The contractor shall provide supplier risk management software to evaluate suppliers.', True),
    ('No projector installation is required, but the contractor shall provide supplier risk management software.', True),
    ('The contractor shall replace previously purchased supplier risk management software.', True),
    ('The requirement requires projectors and refers vendors to guidance about supplier risk management.', False),
    ('Quoted prior work:\nProvide supplier risk management software.', False),
    ('Historical background:\nProvide supplier risk management software.', False),
    ('The following work is not required. Provide supplier risk management software.', False),
    ('The agency requires a response about supplier risk management; the actual scope is only in an unavailable SOW.', None),
])
@pytest.mark.parametrize('adapter', ['direct','extract'])
def test_independent_operator_scope_cases(text, expected, adapter):
    row = extract(text) if adapter == 'extract' else notice(text)
    assert requirement_support(row, TAX)['requested_support'] is expected


def test_retained_title_is_still_title_only():
    assert requirement_support(extract('', 'Provide supplier risk management software'), TAX)['requested_support'] is None


@pytest.mark.parametrize('reverse,alias', [(False,False),(True,False),(False,True),(True,True)])
def test_late_clause_survives_first_boilerplate_title_and_api_projection(reverse, alias):
    positive = 'The contractor shall provide ' + ('vendor' if alias else 'supplier') + ' risk management software and monthly reports.'
    boiler = 'Evaluation uses SPRS supplier risk management information.'
    text = ('Unrelated notice text. ' * 200) + (positive + '\n' + boiler if reverse else boiler + '\n' + positive)
    row = extract(text, 'Supplier risk management')
    support = requirement_support(row, TAX)
    assert support['requested_support'] is True
    own = [s for s in support['spans'] if s['role']=='requested_deliverable']
    assert own and text[own[0]['original_start']:own[0]['original_start']+len(own[0]['matched_text'])] == own[0]['matched_text']
    api = json.loads(map_notice({'noticeId':'N','title':'Platform','description':text}).model_dump_json())
    captured=[]
    engine=Mock()
    def collect(**kw):
        captured.append(kw['context']['notices'][0])
        return TriageResult(items=[TriageItem(id='N',verdict='monitor',reason='Fixture only')])
    engine.deliberate.side_effect=collect
    triage_notices('apexanalytix','fixture',[api],engine=engine,taxonomy=TAX)
    ev=captured[0]['official_source_evidence'][0]['support']
    assert any(positive in s['quote'] for s in ev['spans'])


@pytest.mark.parametrize('mutation', ['source_id','hash','offset','role','truncated','url','field','raw_identity'])
def test_receipts_and_labels_do_not_authenticate_themselves(mutation):
    text='The contractor shall provide supplier risk management software.'
    row=extract(text)
    if mutation=='source_id': row['raw_payload']['screen_evidence_matches'][0]['source_id']='OTHER'
    if mutation=='hash': row['raw_payload']['screen_evidence_matches'][0]['field_sha256']='0'*64
    if mutation=='offset': row['raw_payload']['screen_evidence_matches'][0]['context_start']=1
    if mutation=='field': row['raw_payload']['screen_evidence_matches'][0]['field']='invented'
    if mutation=='raw_identity': row['raw_payload']['notice_id']='OTHER'
    if mutation=='truncated': row['description_truncated']=True
    if mutation=='role': row=notice('Evaluation uses SPRS supplier risk management data.', semantic_role='requested_deliverable', requested_support=True)
    if mutation=='url': row=notice('https://example.test/supplier-risk-management', semantic_role='requested_deliverable')
    assert requirement_support(row,TAX)['requested_support'] is not True


def test_overflow_never_proves_complete_negative_or_positive():
    row=extract('Evaluation uses SPRS supplier risk management data. '*60+'The contractor shall provide supplier risk management software.')
    support=requirement_support(row,TAX)
    assert support['overflow'] and support['requested_support'] is None


@pytest.mark.parametrize('latest_text', ['No supplier risk management software is required.', 'Deliver only projectors.'])
def test_newer_negative_prevents_old_positive_and_family_inflation(latest_text):
    old=notice('The contractor shall provide supplier risk management software.')
    old.update(posted_date='2026-01-01',raw_payload={'solicitation':'S','agency':'A','office':'O'})
    new=copy.deepcopy(old);new.update(source_id='NEW',posted_date='2026-02-01',description=latest_text)
    candidates,ruled,receipt=deterministic_prefilter([old,new],TAX)
    assert not candidates and ruled['N']['superseded_by']=='NEW'
    diag=receipt['requirement_family_diagnostic']
    assert diag['unique_lexical_families']==1
    assert all('requested' not in r['buckets'] for r in diag['families_by_term_field'])


def test_families_do_not_merge_different_offices_or_lanes():
    rows=[]
    for i,(office,lane) in enumerate([('O','sam.gov'),('O','sam.gov'),('P','sam.gov'),('O','other')]):
        r=notice('Provide supplier risk management software.');r.update(source_id=str(i),source=lane,posted_date=f'2026-01-0{i+1}',raw_payload={'solicitation':'S','agency':'A','office':office});rows.append(r)
    _,_,receipt=deterministic_prefilter(rows,TAX)
    assert receipt['requirement_family_diagnostic']['unique_lexical_families']==3


def test_prespecified_family_denominator_four_lexical_three_requested():
    positive=CASES[0]['description'];boiler=CASES[2]['description'];rows=[]
    for sid,sol,text,office in [('B1','B',boiler,'O1'),('B2','B',boiler,'O1'),('P1','P',positive,'O1'),('P2','P',positive+' '+positive,'O1'),('P3','P-call2',positive,'O1'),('P4','P',positive,'O2')]:
        row=notice(text);row.update(source_id=sid,source='sam.gov',agency='Synthetic Agency',raw_payload={'solicitation':sol,'office':office},posted_date='2026-09-12' if sid.endswith('2') else '2026-09-11');rows.append(row)
    _,_,receipt=deterministic_prefilter(rows,TAX)
    counts=receipt['requirement_family_diagnostic']['term_field_counts']
    assert len(counts)==1
    assert counts[0]['field']=='description'
    assert counts[0]['lexical_families']==4 and counts[0]['requested_families']==3


def test_attachment_bundle_keeps_its_discovery_provenance():
    row=notice('Platform procurement.')
    row['raw_payload']={'text':'Provide supplier risk management software.',
                        'attachment_evidence':[{'resource_id':'A','source_url':'https://example.gov/sow','sha256':'abc'}],
                        'attachment_evidence_sha256':'bundle','attachment_inventory_hash':'inventory'}
    support=requirement_support(row,TAX)
    assert support['attachment_context']['files']==row['raw_payload']['attachment_evidence']
    assert support['attachment_context']['bundle_sha256']=='bundle'
    assert support['attachment_context']['per_file_text_offsets']=='NOT_RECORDED'
    assert support['qualification']=='NOT_ESTABLISHED'
