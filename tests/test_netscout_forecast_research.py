"""Saved public requirements through the ordinary forecast producer and consumers."""
import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
import run_searches as rs
from agents.schemas import CapabilityProfile
from agents.reports.horizon import build_fact_bank
from tools.agencies import find
from tools.api.forecasts.dhs_apfs import map_record
from tools.api.forecasts.matching import bucket_forecasts
from tools.api.forecasts.research import discover_forecasts, serialize_forecast_match
from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm, KillRule
CLOCK = datetime(2026, 9, 13, tzinfo=timezone.utc)
CASES = json.loads((Path(__file__).parent/'fixtures/netscout_dhs_research.json').read_text())
RAW = [c['raw_record'] for c in CASES['cases'] if c['case_id'].startswith('APFS-')]
PROFILE = CapabilityProfile(client_name='NETSCOUT', naics_codes=['541519'], set_aside_eligibility=[])
def rows():
    return [map_record(r, retrieved_at=CLOCK) for r in RAW]

def ordinary_forecast_producer(monkeypatch, records, taxonomy=None, engagement_scope=None):
    """Execute production closure verbatim, substituting source I/O only."""
    import tools.api.forecasts as api
    enriched = []
    class Source:
        name = 'dhs_apfs'
        enabled = True
        forecast_agency = 'DHS'
        last_provenance = {'retrieved_at': CLOCK.isoformat(), 'mode': 'fixture', 'complete': True}
        def forecasts(self): return records
        def enrich_matched(self, selected):
            enriched.extend(r.source_id for r in selected)
            return selected
    monkeypatch.setattr(api, 'forecast_sources', lambda: [Source()])
    monkeypatch.setattr(api, 'source_is_offline_safe', lambda source: True)
    tree = ast.parse(Path(rs.__file__).read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_t_forecasts')
    env = dict(vars(rs)) | dict(args=SimpleNamespace(client='NETSCOUT'), naics=['541519'], strategy=SimpleNamespace(set_aside_angles=[]), cap_terms=[], forecast_taxonomy=taxonomy, sam_engagement_scope=engagement_scope, scope_agencies=[find('DHS')], scope_query_agencies=['DHS'], native_workstation=True)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), rs.__file__, 'exec'), env)
    result, _ = env['_t_forecasts']()
    return json.loads(json.dumps(result)), enriched

def test_restricted_tool_sources_survive_ordinary_producer(monkeypatch):
    payload, enriched = ordinary_forecast_producer(monkeypatch, rows())
    research = {r['record']['source_id']: r for r in payload['research_candidates']}
    expected = {'F2026074289', '*F2025071123', 'F2026074410', '*F2026074411'}
    assert expected <= set(research) and expected <= set(enriched)
    assert all(research[i]['direct_prime_set_aside_eligible'] is False for i in expected)
    assert not expected.intersection(r['source_id'] for r in payload['matched'])
    assert all(research[i]['capability_terms'] and research[i]['match_spans'] for i in expected)
    assert all(research[i]['communication_permission'] == 'none' for i in expected)
    for original in rows():
        if original.source_id in research:
            assert research[original.source_id]['record']['source_fields'] == original.model_dump(mode='json')['source_fields']
    assert payload['research_schema_version'] == 1

def test_capability_evidence_survives_serialization_into_horizon():
    rec = rows()[0].model_copy(update={'set_aside':None, 'naics_code':'541519','title':'Packet capture appliance'})
    taxonomy = CapabilityTaxonomy(client_name='NETSCOUT',version=1,updated='2026-09-12',core=[TaxonomyTerm(term='packet capture')])
    match = bucket_forecasts([rec],PROFILE,taxonomy=taxonomy)['capability'][0]
    serialized = json.loads(json.dumps(serialize_forecast_match(match)))
    assert serialized['keyword_hits'] == ['packet capture'] and serialized['match_spans']
    noise = [{'source':'dhs_apfs','source_id':f'noise-{i}', 'reasons':['exact NAICS 541519']} for i in range(11)]
    bank = build_fact_bank({'forecast_signals':{'total_records':12,'matched':[*noise,serialized]}})
    assert [r['source_record_id'] for r in bank if r['kind']=='forecast_line'] == [rec.source_id]

@pytest.mark.parametrize('title,description',[
    ('ArborText maintenance','Technical publication software'),
    ('Arbor Research Collaborative for Health','Provider network study'),
    ('Physical IDS panels','Keypads, alarm-control panels and sensors'),
    ('Network switches','Ordinary switching hardware and cabling'),
    ('COSS support services','Staffing for cybersecurity operations'),
    ('SASS','Student scheduling application'),
    ('AWS cloud consumption','Cloud hosting renewal'),
    ('Passive collection','Biological sample collection'),
])
def test_negative_functions_do_not_create_research(title,description):
    rec=rows()[0].model_copy(update={'title':title,'description':description})
    assert discover_forecasts([rec],PROFILE)==[]

def test_exploratory_competitor_does_not_establish_capability():
    rec=rows()[0].model_copy(update={'title':'Gigamon renewal','description':'License maintenance'})
    result=discover_forecasts([rec],PROFILE)[0]
    assert result['exploratory_terms']==['Gigamon'] and not result['capability_terms']
    assert any(r.get('term')=='Gigamon' and r['disposition']=='exploratory' and r['definition_sha256'] and r['reason'] for r in result['vocabulary'])
    exclusion=CapabilityTaxonomy(client_name='NETSCOUT',version=1,updated='2026-09-12',exclude=[KillRule(term='license maintenance',scope='record')])
    assert discover_forecasts([rec],PROFILE,taxonomy=exclusion)==[]
    with pytest.raises(ValueError,match='different client'):
        discover_forecasts([rec],PROFILE,taxonomy=exclusion.model_copy(update={'client_name':'OTHER'}))

def test_generic_context_does_not_kill_separate_real_tool_requirement():
    rec=rows()[0].model_copy(update={'title':'Physical alarm and network monitoring','description':'Replace physical alarm panels. Separately buy network TAPs for packet replication.'})
    assert discover_forecasts([rec],PROFILE)[0]['capability_terms']==['network tap']


def test_operator_excluded_core_and_exploratory_terms_take_precedence():
    from tools.relevance.taxonomy import RetrievalMapping
    rec=rows()[0].model_copy(update={'title':'Packet capture Gigamon','description':'Gigamon packet capture'})
    tax=CapabilityTaxonomy(client_name='NETSCOUT',version=1,updated='2026-09-12',retrieval_mappings=[RetrievalMapping(term=t,disposition='excluded',reason='Operator excludes this term') for t in ['Gigamon','packet capture']])
    assert discover_forecasts([rec],PROFILE,taxonomy=tax)==[]
    span=tax.model_copy(update={'retrieval_mappings':[], 'exclude':[KillRule(term='Gigamon',scope='span')]})
    assert discover_forecasts([rec],PROFILE,taxonomy=span)==[]
    independent=rec.model_copy(update={'description':'Gigamon. '+('Unrelated background. '*25)+'Buy network TAPs.'})
    assert discover_forecasts([independent],PROFILE,taxonomy=span)[0]['capability_terms']==['network tap']


def test_scope_is_hard_and_off_code_research_is_explicit():
    from tools.relevance.scope import EngagementScope
    from tools.relevance.taxonomy import CodeUniverse
    scope=EngagementScope.model_validate({'departments':['VA']})
    assert discover_forecasts(rows(),PROFILE,engagement_scope=scope)==[]
    tax=CapabilityTaxonomy(client_name='NETSCOUT',version=2,updated='2026-09-12',core=[TaxonomyTerm(term='packet capture')],code_universe=CodeUniverse(naics=['541519']))
    rec=rows()[0].model_copy(update={'title':'Packet capture platform','description':'','naics_code':'611430'})
    found=discover_forecasts([rec],PROFILE,taxonomy=tax)[0]
    assert found['direct_code_boundary']=='excluded'
    assert found['native_boundary_evidence']['excluded_by_code'] is True
    assert found['qualification_effect'].startswith('none')
    assert discover_forecasts([rec],PROFILE)[0]['direct_code_boundary']=='unknown'
