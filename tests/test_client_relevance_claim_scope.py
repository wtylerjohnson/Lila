"""Separately labeled Step3 C1 assertion and native-validation extension."""
import copy
from types import SimpleNamespace
import pytest
from agents.reports import composer
from tools.relevance.engine import score_record
from tools.relevance.taxonomy import load_taxonomy


@pytest.mark.parametrize('text', [
    'In 2019 the agency purchased supplier risk management software.',
    'Evaluation uses SPRS supplier risk management data.',
    'No supplier risk management services are required.',
    'The contractor shall provide supplier risk management software.',
])
def test_c1_context_does_not_assert_current_funding_or_displacement(text):
    tax=load_taxonomy('apexanalytix')
    row={'source_id':'A','description':text,'type':'Award Notice','completion':'2020-01-01'}
    basis=composer._verdict_client_relevance_basis(score_record(row,tax),kind='record-core-match',source_identity='A',public_context={'band':'competitors','buyer':'VA','client':'apexanalytix'})
    public=composer.client_relevance_public(basis)
    assert 'record mentions SUPPLIER RISK MANAGEMENT' in public['text']
    assert 'currently funds' not in public['text'] and 'DISPLACEMENT TARGET' not in public['text']
    assert 'check current funding' in public['text']
    assert composer._client_relevance_support_is_stored(basis['supports'][0],term=basis['term'],kind=basis['kind'],taxonomy=tax,sweep={'results':{'sam.gov':[row]}})
    changed=copy.deepcopy(row);changed['source_id']='B'
    assert not composer._client_relevance_support_is_stored(basis['supports'][0],term=basis['term'],kind=basis['kind'],taxonomy=tax,sweep={'results':{'sam.gov':[changed]}})


def test_validation_strength_is_explicit_and_native_requires_all_inputs():
    content={'composition_mode':'machine','client_name':'apexanalytix'}
    assert composer.validate_machine_client_relevance_contract(content)=='projection_only'
    with pytest.raises(ValueError,match='requires current source'):
        composer.validate_machine_client_relevance_contract(content, require_current_sources=True)
    with pytest.raises(ValueError,match='partial'):
        composer.validate_machine_client_relevance_contract(content,taxonomy=load_taxonomy('apexanalytix'))
    assert composer.validate_machine_client_relevance_contract(content,taxonomy=load_taxonomy('apexanalytix'),profile=SimpleNamespace(client_name='apexanalytix'),sweep={},require_current_sources=True)=='current_sources_rebound'
    assert composer.validate_machine_client_relevance_contract({'composition_mode':'operator'},require_current_sources=True)=='operator_not_applicable'
