"""Development cases, not independent Veeam precision or market-recall proof."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
from agents.assess.contracts import AssessScope, ResearchSubject
from agents.assess.ledger import build_assess_run, persist_assess_run, load_current_assess_run
from agents.assess.research_subjects import make_subject
from agents.assess.upstream import screen_programs, coverage_matrix, sweep_coverage
from agents.leadgen.press import run_press
from agents.leadgen.press_html import render_html
from tools.capability import ClientProfile, CapabilityTerms
from tools.api.base import SourceQuery
import tools.api.watchdog_rss as wd

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
PROFILE = ClientProfile(client_name='Veeam', naics_boundary=['541519'], capability_terms=CapabilityTerms(
    core=['data recovery', 'backup'], adjacent=['cloud migration'], excluded=['traffic backup']))


def row(**changes):
    return dict(dict(source='dod_budget_exhibits', tier='program', kind='budget',
        record_id='test-program', title='Data recovery modernization',
        description='Replace aging data recovery infrastructure.',
        agency='Department of Defense', component='Missile Defense Agency',
        canonical_url='https://www.mda.mil/program/test',
        retrieved_at='2026-09-15T10:00:00Z', data_as_of='2026-09-14',
        values={'requested': 20}, source_fields={'footnote': 'Proposed, not appropriated'}), **changes)


def sweep(*rows):
    return {'client': 'Veeam', 'generated_at': NOW.isoformat(), 'search_scope': {'all': True},
            'results': {'dod_budget_exhibits': {'records': list(rows)}}}


def screen(data):
    return screen_programs(data, PROFILE, scope=AssessScope(), as_of=NOW)


def test_program_evidence_reaches_native_assess_and_render_without_buying_child(tmp_path):
    data = sweep(row())
    binding = {'version': 1, 'scope_designator': 'all', 'profile_sha256': 'fixture-profile',
               'sweep_sha256': 'fixture-sweep', 'sweep_artifact': 'fixture.json'}
    run, diagnostics, index = build_assess_run('Veeam', data, PROFILE, binding, as_of=NOW)
    assert len(run.research.items) == 1
    subject = run.research.items[0]
    assert subject.schema_version == 'assess.research-subject.v2'
    assert json.loads(subject.source_payload_json) == row()
    assert subject.evidence[0].supports == ('buyer',)
    assert subject.evidence[0].retrieved_at is None
    assert subject.communication_permission == 'none'
    assert not any('solicitation' in q.lower() for q in subject.open_questions)
    path = persist_assess_run(run, binding, diagnostics, index, state_dir=tmp_path)
    restored = load_current_assess_run('Veeam', 'all', state_dir=tmp_path)
    receipt = run_press(assess=restored, client_name='Veeam')
    assert len(receipt.parents) == 1 and not receipt.leads
    html = render_html(receipt)
    assert 'Emerging need / research' in html and subject.next_ask in html
    assert 'A request is not an appropriation' in html
    assert 'Proposed, not appropriated' in html
    assert 'match_spans' not in html
    assert path.read_bytes()


@pytest.mark.parametrize('change,reason', [
    ({'canonical_url':'https://trade.example/story'}, 'source_schema_or_primary_evidence_gap'),
    ({'retrieved_at':'2026-09-17T00:00:00Z'}, 'not_available_at_cutoff'),
    ({'data_as_of':'2026-09-17'}, 'source_date_after_cutoff'),
    ({'description':'Cloud migration project', 'title':'Modernization'}, 'no_supported_capability_match'),
    ({'description':'Traffic backup relief', 'title':'Traffic backup relief'}, 'no_supported_capability_match'),
    ({'kind':'news'}, 'source_schema_or_primary_evidence_gap'),
])
def test_false_matches_unavailable_and_secondary_sources_remain_not_leads(change, reason):
    found, decisions = screen(sweep(row(**change)))
    assert found == [] and decisions[0]['reason'] == reason


def test_empty_budget_and_no_rfp_do_not_block_research():
    found, _ = screen(sweep(row(values={})))
    assert len(found) == 1


def test_conflicting_amendments_held_duplicates_collapse_and_originals_preserved():
    first = row()
    data = sweep(first, deepcopy(first))
    assert len(screen(data)[0]) == 1 and len(data['results']['dod_budget_exhibits']['records']) == 2
    data['results']['dod_budget_exhibits']['records'].append(row(title='Canceled project', description='No further work'))
    found, decisions = screen(data)
    assert not found and all(d['reason'] == 'conflicting_versions_need_review' for d in decisions)


def test_foreign_scope_not_a_negative_capability_verdict():
    scope = AssessScope(mode='agency', agencies=({'name':'Department of Homeland Security', 'abbr':'DHS'},))
    found, decisions = screen_programs(sweep(row()), PROFILE, scope=scope, as_of=NOW)
    assert found == [] and decisions[0]['relevant'] is None


def test_missing_sources_and_failed_sources_are_not_zero_opportunities():
    data = sweep(row())
    data['results']['congress'] = {'error':'key unavailable'}
    _, decisions = screen(data)
    matrix = coverage_matrix(data, agencies=['Missile Defense Agency'], decisions=decisions)
    assert len(matrix['rows']) == 7
    budget = matrix['rows'][0]
    assert budget['screened'] == 1 and budget['relevant'] == 1
    assert budget['available'] is None and budget['actionable_lead'] is None
    announcements = matrix['rows'][-1]
    assert announcements['relevant'] is None
    assert any(r.get('error') == 'key unavailable' for r in announcements['receipts'])
    assert sweep_coverage(data, PROFILE)['internal_only']


def test_v2_subject_cannot_be_relabelled_old_contract_or_gain_authority():
    subject = make_subject('program', row())
    raw = subject.model_dump(mode='json')
    raw['schema_version'] = 'assess.research-subject.v1'
    with pytest.raises(ValueError): ResearchSubject.model_validate(raw)
    raw = subject.model_dump(mode='json')
    raw['evidence'][0]['supports'] = ['buyer', 'funding']
    with pytest.raises(ValueError): ResearchSubject.model_validate(raw)


def test_watchdog_full_text_before_filter_and_inventory_before_cap(monkeypatch):
    text = 'Background. ' * 80 + 'Data recovery infrastructure is inadequate.'
    item = '<item><title>Agency finding</title><link>https://www.gao.gov/test</link><description>'+text+'</description></item>'
    monkeypatch.setattr(wd, 'FEEDS', {'GAO':'https://www.gao.gov/rss', 'IG':'https://www.oversight.gov/rss'})
    def fetch(url):
        if 'oversight' in url: raise RuntimeError('unavailable')
        return '<rss><channel>'+item*21+'</channel></rss>'
    monkeypatch.setattr(wd, 'get_text', fetch)
    result = wd.WatchdogRssSource().enrich(SourceQuery(keywords=['data recovery']))
    assert len(result['items']) == 20
    assert len(result['parsed_inventory']) == 21 and result['matched_before_cap'] == 21
    assert result['parsed_inventory'][0]['body'] == text
    assert result['errors'] == {'IG':'unavailable'}
    assert len(result['source_attempts']) == 2


def test_ordinary_forecast_producer_keeps_unmatched_inventory(monkeypatch):
    from tests.test_netscout_forecast_research import ordinary_forecast_producer, rows
    records = rows()
    noise = records[0].model_copy(update={'source_id': 'NOT-RELEVANT',
        'title': 'Backup power generator', 'description': 'Diesel generator replacement'})
    payload, _ = ordinary_forecast_producer(monkeypatch, [*records, noise])
    inventory = payload['parsed_inventory']
    assert len(inventory) == len(records) + 1
    assert inventory[-1]['source_id'] == 'NOT-RELEVANT'
    assert all(c['record']['source_id'] != 'NOT-RELEVANT' for c in payload['research_candidates'])


def test_dhs_only_inventory_does_not_report_zero_mda_opportunities():
    data = {'results': {'forecast_signals': {'parsed_inventory': [
        {'source': 'dhs_apfs', 'agency': 'DHS', 'component': 'CBP/HQ'}]}}}
    matrix = coverage_matrix(data, agencies=['Missile Defense Agency', 'CBP'])
    mda = next(r for r in matrix['rows'] if r['agency_or_component'] == 'Missile Defense Agency'
               and r['family'] == 'acquisition_planning')
    assert all(mda[k] is None for k in ('collected', 'parsed', 'screened', 'relevant',
                                     'native_research', 'investigated', 'actionable_lead'))
    cbp = next(r for r in matrix['rows'] if r['agency_or_component'] == 'CBP'
               and r['family'] == 'acquisition_planning')
    assert cbp['collected'] == 1 and cbp['relevant'] == 0


def test_reviewed_negative_is_investigated_but_not_an_actionable_lead(monkeypatch):
    from tests.test_native_action_sheets import reviewed_parent
    from agents.assess.reviewed_cases import ReviewedSubject
    receipt, parent = reviewed_parent(monkeypatch)
    subject = parent.research_subject
    research = parent.research.model_copy(update={'status': 'deprioritized', 'priority': False})
    overlay = ReviewedSubject(subject_id=subject.subject_id, source_sha256=subject.source_sha256, research=research)
    parent = parent.model_copy(update={'research': research, 'research_subject': subject.model_copy(update={
        'reviewed_overlay_json': overlay.model_dump_json()})})
    matrix = coverage_matrix({}, agencies=[subject.agency], parents=[parent])
    investigated = [r for r in matrix['rows'] if r['investigated']]
    assert len(investigated) == 1 and investigated[0]['investigated'] == 1
    assert investigated[0]['actionable_lead'] is None
    detached = parent.model_copy(update={'research_subject': subject.model_copy(update={'reviewed_overlay_json': None})})
    matrix = coverage_matrix({}, agencies=[subject.agency], parents=[detached])
    assert not any(r['investigated'] for r in matrix['rows'])


def test_withdrawal_is_visible_not_a_new_buying_window():
    from tests.test_forecast_pocs import mapped
    from agents.leadgen.action_sheet import research_subject_brief
    from tools.api.forecasts.posture import withdrawal_evidence
    raw = mapped().model_dump(mode='json')
    raw.update(source='other_forecast', source_id='WITHDRAWN', url='https://example.gov/forecast/1',
               description='This requirement is no longer planned. Republished for industry awareness.')
    subject = make_subject('forecast', raw)
    assert 'Do not respond to the withdrawn plan' in subject.next_ask
    assert 'Historical planning context only' in research_subject_brief(subject)
    assert withdrawal_evidence({'description': 'Cancellation of old test tasks does not affect the new requirement.'}) is None
    assert subject.communication_permission == 'none'


def test_real_record_replay_is_frozen_and_not_a_lead_count():
    from tools.evaluate_upstream import replay
    from pathlib import Path
    from tests.test_netscout_forecast_research import RAW
    from datetime import timedelta
    capability = json.loads((Path(__file__).parents[1] /
        'docs/verification/upstream-leads-060/veeam-capability-input.json').read_text())
    a, ah = replay(RAW, capability, captured_at=NOW, as_of=NOW)
    b, bh = replay(RAW, capability, captured_at=NOW, as_of=NOW)
    assert a == b and ah == bh
    assert a['metrics.json']['useful_lead_yield'] is None
    assert a['metrics.json']['qualified_buying_children'] == 0
    with pytest.raises(ValueError, match='postdates'):
        replay(RAW, capability, captured_at=NOW, as_of=NOW - timedelta(days=1))


def test_capability_alias_preserves_weak_keyword_evidence_without_qualification():
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm, PhraseAlias, CapabilityEvidence
    data = sweep(row(title='Continuity exercise', description='Recovery drills for protected data have not been tested.'))
    tax = CapabilityTaxonomy(client_name='Veeam', version=1, updated='2026-09-16',
        evidence=[CapabilityEvidence(evidence_id='fixture', url='https://example.com/capability',
            reviewed_at='2026-09-16', basis='Synthetic unit-test capability definition, not real-client proof')], core=[
        TaxonomyTerm(term='data recovery', evidence_ids=['fixture'],
            aliases=[PhraseAlias(phrase='recovery drills', context_any=['data'])])])
    data['results']['upstream_vocabulary'] = tax.model_dump(mode='json')
    found, _ = screen(data)
    assert len(found) == 1 and found[0][1]['qualification_effect'] == 'none'
    assert any(s['matched_text'] == 'Recovery drills' for s in found[0][1]['match_spans'])


def test_program_reaches_eight_slot_future_demand_not_historical_spending(tmp_path):
    from agents.assess.reviewed_cases import ReviewedCases
    from agents.golden_press.notice_bridge import NoticeReadContext, digest
    from agents.golden_press.external_product_projection import build_external_product_document
    from agents.golden_press.external_product_render import _research_record
    import hashlib
    binding = {'version':1, 'scope_designator':'all', 'profile_sha256':'fixture',
               'sweep_sha256':'fixture', 'sweep_artifact':'fixture.json'}
    run, _, _ = build_assess_run('Veeam', sweep(row()), PROFILE, binding, as_of=NOW)
    receipt = run_press(assess=run, client_name='Veeam')
    context = NoticeReadContext.__new__(NoticeReadContext)
    context.current_assess_run = run
    context.current_reviewed_cases = ReviewedCases(client_name='Veeam')
    context.client_name, context.as_of = 'Veeam', NOW
    context.gaps, context._projection_inputs = [], None
    # Same fixture boundary as the existing native-action projection tests.
    current = tmp_path / 'current-inputs.json'
    current.write_text(run.model_dump_json())
    context.files = {str(current):hashlib.sha256(current.read_bytes()).hexdigest()}
    graph = {'records':[], 'qualified_opportunity_records':[]}
    context._graph_sha256 = digest(graph)
    companion = {'status':'complete', 'assess_run_id':run.run_id,
        'assessment':{'run':run.model_dump(mode='json')}, 'receipt':receipt.model_dump(mode='json')}
    doc = build_external_product_document(market_map={}, graph_payload=graph, evidence_pack={},
        profile={}, client_name='Veeam', slug='veeam', as_of=NOW.date().isoformat(),
        leadgen=companion, notice_context=context)
    owned = [(s.slot_id, r) for s in doc.slots[1:] for r in s.records if r.get('research_subject')]
    assert len(doc.slots) == 8 and len(owned) == 1
    slot, projected = owned[0]
    assert slot == 'future-forecasts' and projected['kind'] == 'program'
    assert projected['response_date'] is None and projected['value'] is None
    assert not projected.get('lead_rows')
    html = _research_record(projected)
    assert 'Emerging need / research' in html and 'Existing contract / research' not in html
    assert 'Proposed, not appropriated' in html and 'match_spans' not in html
