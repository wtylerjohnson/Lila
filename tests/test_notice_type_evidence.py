"""S2-HISTORY-001: actual adapters and projection conflicts, offline."""
import io
import json
import pytest

from tools.api.sam_extract import iter_rows, _to_opportunity
from tools.api.sam_gov import map_notice
from tools.relevance.notice_type import notice_type_evidence
from tools.relevance.taxonomy import load_taxonomy
from agents.decisions.triage import deterministic_prefilter


@pytest.mark.parametrize('label', ['Award Notice', 'award', 'a', 'awards', 'Award Notification', 'Awarded', '  aWaRd   NoTiFiCaTiOn  '])
@pytest.mark.parametrize('adapter,field', [('api', 'type'), ('api', 'baseType'), ('csv', 'Type'), ('csv', 'BaseType'), ('top', 'notice_type'), ('top', 'type')])
def test_historical_labels_through_actual_adapters(label, adapter, field):
    if adapter == 'api':
        row = json.loads(map_notice({'noticeId': 'H', 'title': 'Supplier risk management', field: label, 'responseDeadLine': '2099-01-01'}).model_dump_json())
    elif adapter == 'csv':
        parsed = next(iter_rows(io.StringIO(f'NoticeId,Title,{field},ResponseDeadLine\nH,Supplier risk management,{label},2099-01-01\n')))
        row = json.loads(_to_opportunity(parsed).model_dump_json())
    else:
        row = {'source_id': 'H', 'title': 'Supplier risk management', field: label, 'response_deadline': '2099-01-01'}
    before = json.dumps(row, sort_keys=True)
    candidates, ruled, receipt = deterministic_prefilter([row], load_taxonomy('apexanalytix'))
    assert not candidates and ruled['H']['screen_state'] == 'HISTORICAL_MARKET_EVIDENCE'
    types = receipt['screening_records']['H']['notice_type_evidence']
    assert types['historical'] and types['selected_field']
    assert json.dumps(row, sort_keys=True) == before


@pytest.mark.parametrize('label', ['Preaward', 'pre-award', 'contract', 'Solicitation', 'Combined Synopsis/Solicitation', 'unknown', '', None])
def test_controls_are_not_historical(label):
    row = {'type': label, 'description': 'A prior award purchased software.'}
    assert not notice_type_evidence(row)['historical']


def test_native_type_whitespace_projection_conflict_and_base_provenance():
    row = {'notice_type': '  ', 'raw_payload': {'type': 'Awarded', 'baseType': 'Solicitation'}}
    ev = notice_type_evidence(row)
    assert ev['selected_field'] == 'raw_payload.type' and not ev['conflict']
    assert ev['fields'][2]['raw'] == 'Solicitation'
    row['notice_type'] = 'Solicitation'
    assert notice_type_evidence(row)['conflict']
    row = {'type': 'Sources Sought', 'raw_payload': {'type': 'Solicitation'}}
    _, ruled, _ = deterministic_prefilter([dict(row, source_id='C', title='supplier risk management')], load_taxonomy('apexanalytix'))
    assert ruled['C']['verdict'] == 'unscreened'


def test_native_current_type_does_not_inherit_historical_base_type():
    ev = notice_type_evidence({'raw_payload': {'type': 'Solicitation', 'baseType': 'Award Notice'}})
    assert not ev['historical'] and not ev['conflict']
    assert ev['fields'][1]['classification'] == 'historical'
