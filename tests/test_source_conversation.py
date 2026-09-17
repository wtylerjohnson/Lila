"""Development acceptance cases; these are not held-out market-yield proof."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from agents.assess import investigations as worker
from agents.assess.ledger import build_assess_run
from agents.assess.research_subjects import make_subject
from agents.assess.upstream import screen_programs
from agents.assess.contracts import AssessScope, ResearchSubject
from tools.api.base import SourceQuery
from tools.api.source_mesh import _canonical_bytes, nested_program_evidence
from tools.capability import ClientProfile, CapabilityTerms

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
PROFILE = ClientProfile(client_name='Veeam', naics_boundary=['541519'],
                        capability_terms=CapabilityTerms(core=['data recovery']))


def row(identity='one', **changes):
    return dict(dict(source='agency_program_documents', tier='program', kind='budget',
        record_id=identity, program_id=identity, title='Enterprise data recovery',
        description='Data recovery infrastructure needs replacement. Funding is requested.',
        agency='Missile Defense Agency', component='Missile Defense Agency',
        canonical_url='https://www.mda.mil/program/' + identity,
        retrieved_at='2026-09-16T10:00:00Z', data_as_of='2026-04'), **changes)


def sweep(*rows):
    return {'client': 'Veeam', 'generated_at': NOW.isoformat(), 'search_scope': {'all': True},
            'results': {'agency_program_documents': {'records': list(rows)}}}


def draft(bundle, **changes):
    e = bundle['evidence'][0]
    return dict(dict(decision='pursue', buyer_need='Published replacement need; current work unknown',
        fit_hypothesis='Data recovery may fit; product/environment confirmation needed',
        why_now='FY2027 request is a planning signal; no confirmed appropriation',
        route='Unknown; ask the program office for technical ownership',
        rationale='Investigate the independently identified program need',
        first_question='Who owns the recovery requirement and what remains unresolved?',
        next_action='Research the program office routing contact before outreach',
        unknowns=['Technical owner', 'Funding and current work'],
        evidence=[{'evidence_id': e['evidence_id'], 'quote': e['excerpt'][:100]}],
        contacts=[]), **changes)


def test_timeout_is_durable_and_resume_is_bounded_idempotent(tmp_path):
    data = sweep(row(), row('two'))
    calls = []
    def first(bundle):
        calls.append(bundle['source']['record_id'])
        if calls[-1] == 'two':
            raise TimeoutError('single subject timed out')
        return draft(bundle)
    one = worker.run_investigations(data, PROFILE, root=tmp_path, synth=first)
    assert [x['state'] for x in one['items']] == ['complete', 'failed']
    assert 'overlay' not in one['items'][1]
    two = worker.run_investigations(data, PROFILE, root=tmp_path, synth=first)
    assert calls == ['one', 'two', 'two']
    assert two['items'][0]['overlay'] == one['items'][0]['overlay']
    three = worker.run_investigations(data, PROFILE, root=tmp_path, synth=first)
    assert three['calls_this_run'] == 0
    assert len(three['items'][1]['attempts']) == 2
    assert (tmp_path / 'veeam/all/reviewed/veeam.reviewed_cases.json').exists()
    assert not (tmp_path / 'veeam.reviewed_cases.json').exists()


def test_completed_investigation_reaches_existing_native_renderer(tmp_path):
    from agents.leadgen.press import run_press
    from agents.leadgen.press_html import render_html
    data = sweep(row())
    data['results']['upstream_investigations'] = worker.run_investigations(data, PROFILE, root=tmp_path, synth=draft)
    binding = {'version': 1, 'scope_designator': 'all', 'profile_sha256': 'fixture',
               'sweep_sha256': 'fixture', 'sweep_artifact': 'fixture.json'}
    run, _, _ = build_assess_run('Veeam', data, PROFILE, binding, as_of=NOW)
    assert run.research.items[0].reviewed_overlay_json
    pressed = run_press(assess=run, client_name='Veeam')
    assert not pressed.leads and len(pressed.parents) == 1
    html = render_html(pressed)
    assert 'Who owns the recovery requirement' in html
    assert 'single subject' not in html and 'bundle_sha256' not in html
    assert 'Named technical owner' in html


def test_retry_reuses_captured_stores_until_source_binding_changes(tmp_path, monkeypatch):
    from agents.assess import investigation_inputs as inputs
    data=sweep(row())
    worker.run_investigations(data, PROFILE, root=tmp_path, synth=draft)
    captured=deepcopy(data['results']['upstream_investigation_inputs'])
    real_capture=inputs.capture
    def unavailable(*args, **kwargs):
        raise AssertionError('retry must not reopen external stores')
    monkeypatch.setattr(inputs,'capture',unavailable)
    assert worker.run_investigations(data, PROFILE, root=tmp_path, synth=draft)['calls_this_run']==0
    assert data['results']['upstream_investigation_inputs']==captured
    monkeypatch.setattr(inputs,'capture',real_capture)
    data['results']['agency_program_documents']['records'][0]['description']+=' Scope updated.'
    worker.run_investigations(data, PROFILE, root=tmp_path, synth=draft)
    assert data['results']['upstream_investigation_inputs']['input_binding']!=captured['input_binding']


def test_changed_supporting_source_invalidates_reuse_and_historical_cutoff(tmp_path):
    data = sweep(row(), row('fee-rule', program_id='one', description='Biometric fee rule', kind='regulation'))
    result = worker.run_investigations(data, PROFILE, root=tmp_path, synth=draft)
    assert result['calls_this_run'] == 1
    data['results']['agency_program_documents']['records'][1]['description'] = 'Changed fee rule'
    assert worker.run_investigations(data, PROFILE, root=tmp_path, synth=draft)['calls_this_run'] == 1
    subject = make_subject('program', row())
    data['results']['agency_program_documents']['records'][1]['retrieved_at'] = '2026-09-18T00:00:00Z'
    assert len(worker.bundle_for(subject, data, PROFILE, NOW)['evidence']) == 1


@pytest.mark.parametrize('claim', ['The award is imminent.', 'The award is tomorrow.',
    'The procurement window has likely closed.', 'The only actionable route is urgent outreach.'])
def test_forecast_estimates_cannot_create_urgency(claim):
    from tools.api.forecasts.dhs_apfs import map_record
    record = map_record({'id':74554, 'apfs_number':'74554', 'organization':'USSS',
        'requirements_title':'Private cloud infrastructure refresh',
        'requirement':'Replace the hypervisor infrastructure hardware.'}, retrieved_at=NOW)
    subject = make_subject('forecast', record.model_dump(mode='json'), as_of=NOW)
    bundle = worker.bundle_for(subject, sweep(), PROFILE, NOW)
    with pytest.raises(ValueError, match='forecast dates'):
        worker.bind_draft(subject, bundle, worker.InvestigationDraft.model_validate(draft(bundle, why_now=claim)), NOW)
    valid = draft(bundle, why_now='The forecast estimated a September award; actual solicitation and decision status remain unknown.')
    assert worker.bind_draft(subject, bundle, worker.InvestigationDraft.model_validate(valid), NOW)


def test_exact_quote_and_contact_binding_refuse_fabrication():
    subject = make_subject('program', row())
    bundle = worker.bundle_for(subject, sweep(row()), PROFILE, NOW)
    bad = draft(bundle, evidence=[{'evidence_id': bundle['evidence'][0]['evidence_id'], 'quote': 'An enacted appropriation'}])
    with pytest.raises(ValueError, match='quote'):
        worker.bind_draft(subject, bundle, worker.InvestigationDraft.model_validate(bad), NOW)
    bad = draft(bundle, contacts=[dict(contact_id='invented', reason_to_contact='Ask', first_question='Owner?')])
    with pytest.raises(ValueError, match='contact'):
        worker.bind_draft(subject, bundle, worker.InvestigationDraft.model_validate(bad), NOW)


def test_passage_selection_preserves_typography_and_refuses_splicing():
    refs=[{'evidence_id':'a','excerpt':"Director of CBP’s Office. " + ' '.join(f'word{i}' for i in range(60))},
          {'evidence_id':'b','excerpt':'Another independent source with no shared identity.'}]
    _,bank=worker._passage_bank(refs)
    quote=worker._resolve_passages({'passage_ids':['E1:P1','E1:P2']},bank)
    assert 'CBP’s' in quote['quote'] and quote['quote'] in refs[0]['excerpt']
    for ids in (['invented'],['E1:P1','E1:P3'],['E1:P1','E2:P1'],['E1:P2','E1:P1']):
        with pytest.raises(ValueError):worker._resolve_passages({'passage_ids':ids},bank)


def test_official_rule_contact_is_reused_without_transcribing_identity():
    text=('FOR FURTHER INFORMATION CONTACT: Jane Example, Program Office, Example Agency, '
          'by phone at 202–555–0100 or email at JANE.EXAMPLE@EXAMPLE.GOV. SUPPLEMENTARY INFORMATION: Data recovery.')
    subject=make_subject('program',row(description=text))
    bundle=worker.bundle_for(subject,sweep(row(description=text)),PROFILE,NOW)
    person=next(iter(bundle['contacts'].values()))
    assert person['name']=='Jane Example' and person['role']=='Program Office'
    assert person['email']=='JANE.EXAMPLE@EXAMPLE.GOV' and person['phone']=='202–555–0100'
    assert 'not established as mobile' in person['contact_status']
    changed=row(description=text.replace('Program Office','Human Resources'))
    assert not worker.bundle_for(make_subject('program',changed),sweep(changed),PROFILE,NOW)['contacts']


def test_model_selects_exact_source_passages_instead_of_retyping_quotes(monkeypatch):
    from agents.decisions import maxplan_cli
    source=row(description='The agency’s published data recovery requirement remains a planned activity.')
    subject=make_subject('program',source)
    bundle=worker.bundle_for(subject,sweep(source),PROFILE,NOW)
    wire=draft(bundle)
    wire['evidence']=[{'passage_ids':['E1:P1']}]
    prompts=[]
    def call(prompt,**kwargs):
        prompts.append(prompt)
        return json.dumps(wire)
    monkeypatch.setenv('LILA_LLM_ROUTE','max')
    monkeypatch.setattr(maxplan_cli,'run_claude',call)
    selected=worker.synthesize(bundle)
    assert selected.evidence[0].quote in worker._compact(bundle['evidence'][0]['excerpt'])
    assert 'agency’s' in selected.evidence[0].quote
    assert worker.bind_draft(subject,bundle,selected,NOW)
    assert 'passage_ids' in prompts[0]


def test_published_contact_details_require_exact_source_and_hr_is_not_buying():
    text = 'Larry Panetta, Director, Biometric Entry Exit, larry.panetta@cbp.dhs.gov, (202) 555-0100. Data recovery.'
    r = row(description=text)
    subject = make_subject('program', r)
    b = worker.bundle_for(subject, sweep(r), PROFILE, NOW)
    contact = dict(name='Larry Panetta', role='Director, Biometric Entry Exit',
        email='larry.panetta@cbp.dhs.gov', phone='2025550100',
        evidence={'evidence_id': b['evidence'][0]['evidence_id'], 'quote': text},
        reason_to_contact='Route to the current technical owner', first_question='Who owns current recovery work?')
    d = worker.InvestigationDraft.model_validate(draft(b, published_contacts=[contact]))
    target = worker.bind_draft(subject, b, d, NOW).targets[0]
    assert 'unconfirmed' in target.authority_boundary
    contact['email'] = 'invented@cbp.dhs.gov'
    with pytest.raises(ValueError, match='email'):
        worker.bind_draft(subject, b, worker.InvestigationDraft.model_validate(draft(b, published_contacts=[contact])), NOW)


def test_nested_hash_truncation_and_account_grain_are_separate():
    source = 'omb_public_budget_database'
    raw = {'accounts': [{'record_id': str(n), 'agency_name': 'Department of Homeland Security',
                        'account_name': 'Operations', 'url': 'https://www.whitehouse.gov/budget.xlsx'} for n in range(50)],
           'total_matched': 231, 'truncated': True, '_provenance': {'data_as_of': '2027'}}
    child = dict(source_id=source, payload=raw, content_sha256=hashlib.sha256(_canonical_bytes(raw)).hexdigest(),
        record_count=50, retrieved_at='2026-09-16T10:00:00Z', status='success',
        retrieval_query={'agencies': ['DHS', 'MDA']})
    data = sweep()
    data['results']['source_mesh'] = {'sources': {source: child}}
    envelopes, errors = nested_program_evidence(data['results'])
    assert not errors and envelopes[0].total_matched == 231 and envelopes[0].truncated
    found, decisions = screen_programs(data, PROFILE, scope=AssessScope(), as_of=NOW)
    assert not found and len(decisions) == 50
    assert all(d['screening_state'] == 'unsupported' for d in decisions)
    raw['accounts'].pop()
    assert nested_program_evidence(data['results'])[1][0]['state'] == 'failed'


def test_oversight_scope_survives_keyword_cap_and_wrong_buyer_is_visible():
    from tools.api.oversight_gov_reports import _query_params, OversightGovReportsSource
    query = SourceQuery(agencies=['DHS', 'MDA'], keywords=['a', 'b', 'c', 'd', 'e'])
    params, capped = _query_params(query)
    assert 'DHS' in params['search_api_fulltext'] and 'MDA' in params['search_api_fulltext'] and capped
    html = '''Displaying 1 - 1 of 1 <tr class="listing-table__row table-row">
    <td data-label="Report Title">Data recovery</td><td data-label="Agency Reviewed / Investigated">Department of Commerce</td>
    <a href="/reports/audit/one">View Report</a></tr>'''
    urls = []
    def fetch(url, **kwargs):
        urls.append(url)
        return html.encode(), {}
    result = OversightGovReportsSource(fetcher=fetch).reports(query)
    assert len(urls) == 2 and not result['items']
    assert result['scope_mismatches'] and result['_provenance']['status'] == 'partial'
    assert result['total_matched'] is None


def test_scoped_vocabulary_excludes_housing_but_retains_digital_neighbor():
    from tools.relevance.taxonomy import load_taxonomy
    from tools.relevance.engine import score_record
    taxonomy = load_taxonomy('Veeam')
    assert not score_record({'description': 'Disaster Recovery Reform Act housing assistance'}, taxonomy).relevant
    assert score_record({'description': 'IT systems require contingency testing and disaster recovery.'}, taxonomy).relevant
    assert score_record({'description': 'Disaster Recovery Reform Act housing assistance.\n\nSeparate IT systems require data recovery.'}, taxonomy).relevant


def test_apfs_additive_fields_keep_archival_nulls_but_reject_invented_values():
    from tools.api.forecasts.dhs_apfs import map_record
    raw = {'id': 123, 'apfs_number': 74811, 'requirements_title': 'COSS cloud operations',
           'requirement': 'Hybrid cloud operations', 'contractor': 'Leidos',
           'contract_number': '47QTCK18D0008 / 70RCSJ26FR0000006', 'organization': 'CISA'}
    mapped = map_record(raw, retrieved_at=NOW).model_dump(mode='json')
    assert mapped['incumbent_stated'] == 'Leidos'
    assert mapped['predecessor_contract_id'] == raw['contract_number']
    make_subject('forecast', mapped)
    legacy = {**mapped, 'incumbent_stated': None, 'predecessor_contract_id': None}
    make_subject('forecast', legacy)
    with pytest.raises(ValueError, match='incumbent'):
        make_subject('forecast', {**mapped, 'incumbent_stated': 'Invented corporation'})


def test_document_html_identity_date_and_scope_are_verified():
    from tools.api.agency_program_documents import parse_document, DOCUMENTS, _matches_scope
    spec = DOCUMENTS[-1]
    receipt = {'final_url': spec['url'], 'retrieved_at': NOW.isoformat()}
    blob = b'<main>September 1, 2026. Panetta. Biometric program activity.</main>'
    doc = parse_document(spec, blob, receipt, blob, receipt)
    assert doc['data_as_of'] == '2026-09-01' and doc['current_remaining_work'] == 'not_established'
    with pytest.raises(ValueError, match='date'):
        parse_document(spec, blob.replace(b'September 1, 2026', b'August 1, 2026'), receipt, blob, receipt)
    assert _matches_scope(DOCUMENTS[0], ['MDA'])
    assert not _matches_scope(DOCUMENTS[0], ['Army'])


def test_tls_retry_keeps_failure_and_does_not_retry_http_errors(tmp_path, monkeypatch):
    from tools.api import agency_program_documents as docs
    def bad_client(timeout):
        raise RuntimeError('CERTIFICATE_VERIFY_FAILED')
    monkeypatch.setattr(docs._http, '_client', bad_client)
    monkeypatch.setattr(docs, '_system_fetch', lambda url, cap: (b'official bytes', {
        'final_url': url, 'redirects': [url], 'http_status': 200}))
    blob, receipt = docs.fetch_document('https://www.mda.mil/test.pdf', root=tmp_path)
    assert blob == b'official bytes'
    assert [a['state'] for a in receipt['attempts']] == ['failed', 'success']
    assert receipt['attempts'][1]['tls_verified'] is True
    assert json.loads(next((tmp_path / 'attempts').glob('*.json')).read_text())['raw_sha256'] == hashlib.sha256(blob).hexdigest()
    def unavailable(timeout):
        raise TimeoutError('source timed out')
    monkeypatch.setattr(docs._http, '_client', unavailable)
    monkeypatch.setattr(docs, '_system_fetch', lambda *args: pytest.fail('timeouts must not masquerade as TLS retries'))
    with pytest.raises(TimeoutError):
        docs.fetch_document('https://www.mda.mil/test.pdf', root=tmp_path / 'timeout')
    failed = json.loads(next((tmp_path / 'timeout/attempts').glob('*.json')).read_text())
    assert failed['status'] == 'failed' and 'raw_sha256' not in failed


def test_exact_historical_order_join_preserves_contracting_role(tmp_path, monkeypatch):
    from tools.notice_store import connect
    from tools.api.forecasts.dhs_apfs import map_record
    from agents.assess.investigation_inputs import capture
    db = tmp_path / 'notices.db'
    with connect(db) as conn:
        conn.execute('INSERT INTO notices (notice_id, title, award_number, sol_number, description_prefix, url, poc_name, poc_title, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('coss', 'COSS historical justification', '70RCSJ26FR0000006', 'FY26-0046',
             'Order 70RCSJ26FR0000006 under parent 47QTCK18D0008. Award February 26, 2026.',
             'https://sam.gov/opp/coss/view', 'Morgan Skaggs', 'Contracting Officer', '2026-03-01', '2026-09-12'))
    monkeypatch.setenv('LILA_INVESTIGATION_NOTICE_STORE_PATH', str(db))
    forecast = map_record({'id': 74811, 'apfs_number': 'F2026074811',
        'requirements_title': 'COSS', 'requirement': 'Hybrid cloud operations',
        'contract_number': '47QTCK18D0008 / 70RCSJ26FR0000006', 'organization': 'CISA'}, retrieved_at=NOW)
    subject = make_subject('forecast', forecast.model_dump(mode='json'))
    result = capture([subject], PROFILE, NOW)['subjects'][subject.subject_id]
    assert result['contract_join']['state'] == 'matched'
    assert set(result['contract_join']['records'][0]['matched_contract_ids']) == {'47QTCK18D0008', '70RCSJ26FR0000006'}
    target = next(iter(result['contacts'].values()))
    assert target['name'] == 'Morgan Skaggs' and 'not established as technical owner' in target['authority_boundary']
    assert 'FY26-0046' in result['evidence'][0]['excerpt']


def test_saved_apollo_preserves_catchall_and_never_promotes_switchboard(tmp_path, monkeypatch):
    from agents.golden_press.targets_store import write
    from agents.assess.investigation_inputs import capture
    monkeypatch.setenv('LILA_TARGETS_STORE_DIR', str(tmp_path))
    saved = dict(contact_id='saved-1', spec_id='routing', join_record_ids=['one'],
        name='Saved Person', title='Cloud Architect', organization='MDA', email='saved@example.gov',
        phone='6146920299', email_status='verified', email_domain_catchall=True,
        screen={'state': 'accepted_with_flags', 'cautions': ['Current role unconfirmed']},
        provenance={'class': 'apollo', 'retrieved_at': '2026-09-16', 'source': 'saved Apollo contacts'})
    write('veeam', {'receipt': {'mode': 'saved'}, 'contacts': [saved, dict(saved,
        contact_id='conflict', name='Unresolved CTO', role_conflict=True, updated_at='2026-09-17')]})
    subject = make_subject('program', row())
    result = capture([subject], PROFILE, NOW)['subjects'][subject.subject_id]
    assert len(result['contacts']) == 1
    contact = next(iter(result['contacts'].values()))
    assert 'verified' in contact['contact_status'] and 'catch-all: True' in contact['contact_status']
    assert contact['phone'] is None and 'unconfirmed' in contact['authority_boundary']
    assert not result['evidence'][0]['primary_source']


def test_exact_award_resolver_separates_empty_failure_and_order_parent():
    from tools.api.research_awards import enrich_forecast_contracts
    forecast = {'research_candidates': [{'record': {'source_id': 'COSS', 'source_fields': {
        'contract_number': '47QTCK18D0008 / 70RCSJ26FR0000006'}}}]}
    def resolve(piid):
        if piid == '47QTCK18D0008':
            return []
        return [{'Award ID': piid, 'generated_internal_id': 'CONT_AWD_' + piid + '_123'}]
    def detail(url):
        return {'piid': '70RCSJ26FR0000006', 'generated_unique_award_id': 'CONT_AWD_70RCSJ26FR0000006_123',
                'date_signed': '2026-02-26', 'period_of_performance': {'start_date': '2026-03-01'},
                'parent_award': {'piid': '47QTCK18D0008'}}
    result = enrich_forecast_contracts(forecast, resolver=resolve, fetch=detail)
    assert [a['state'] for a in result['attempts']] == ['success_empty', 'matched']
    record = result['records'][0]
    assert record['relationship'] == 'order'
    assert record['record']['date_signed'] != record['record']['period_of_performance']['start_date']
    def failure(_):
        raise TimeoutError('transport timeout')
    failed = enrich_forecast_contracts(forecast, resolver=failure, fetch=detail)
    assert not failed['records'] and all(a['state'] == 'failed' for a in failed['attempts'])


def test_program_join_does_not_resurrect_title_similar_cancelled_rfp():
    md30 = row('MD30', title='Enterprise IT support', description='Data recovery and IT modernization')
    mioes = row('MIOES', title='Enterprise IT support', description='Specific RFP cancelled, despite earlier pause and later recruiting.')
    subject = make_subject('program', md30)
    bundle = worker.bundle_for(subject, sweep(md30, mioes), PROFILE, NOW)
    assert len(bundle['evidence']) == 1 and 'cancelled' not in bundle['evidence'][0]['excerpt']


def test_oversight_observation_dates_and_unnamed_components_stay_raw():
    source = 'oversight_gov_reports'
    report = {'record_id': 'OIG-26-20', 'agency_reviewed': 'Department of Homeland Security',
        'title': 'FY2025 FISMA', 'date_issued': '2026-08-12', 'observation_date': '2025-05-31',
        'description': 'Four systems at two unnamed components lacked tested IT contingency plans.',
        'citation_url': 'https://www.oig.dhs.gov/reports/OIG-26-20.pdf'}
    raw = {'items': [report], '_provenance': {}}
    child = {'source_id': source, 'payload': raw, 'record_count': 1, 'status': 'success',
        'retrieved_at': NOW.isoformat(), 'content_sha256': hashlib.sha256(_canonical_bytes(raw)).hexdigest()}
    envelopes, errors = nested_program_evidence({'source_mesh': {'sources': {source: child}}})
    assert not errors
    item = envelopes[0].records[0]
    assert item['data_as_of'] == '2026-08-12'
    assert item['source_record']['observation_date'] == '2025-05-31'
    assert item['component'] is None and item['recommendation_status'] == 'not_retrieved'


def test_collection_failure_cannot_become_screened_zero():
    from run_searches import _empty_sam_decision_receipt
    for results in ({}, {'sam.gov': {'error': 'TLS failed'}}, {'sam.gov': [], 'sam_census': {'complete': False}},
                    {'sam.gov': [], 'sam_census': {'complete': True}, '_attempts': [{'source': 'sam.gov', 'ok': True, 'partial': True}]}):
        receipt = _empty_sam_decision_receipt(results)
        assert receipt['complete'] is False and receipt['candidate_census'] is None
        assert receipt['verdict'] != 'screened-zero'
    good = _empty_sam_decision_receipt({'sam.gov': [], 'sam_census': {'complete': True}})
    assert good['complete'] and good['candidate_census'] == 0


def test_published_contact_can_bind_complementary_role_from_second_publication():
    r = row(description='Larry Panetta, Office of Field Operations, LARRY.A.PANETTA@CBP.DHS.GOV. Data recovery.')
    role = row('activity', program_id='one', kind='agency_announcement',
               description='Larry Panetta, Director of Biometric Entry-Exit, described program activity.')
    subject = make_subject('program', r)
    bundle = worker.bundle_for(subject, sweep(r, role), PROFILE, NOW)
    contact = dict(name='Larry Panetta', role='Director of Biometric Entry-Exit',
        email='LARRY.A.PANETTA@CBP.DHS.GOV',
        evidence={'evidence_id': bundle['evidence'][0]['evidence_id'], 'quote': r['description']},
        role_evidence={'evidence_id': bundle['evidence'][1]['evidence_id'], 'quote': role['description']},
        reason_to_contact='Request technical routing', first_question='Who owns recovery?')
    result = worker.bind_draft(subject, bundle, worker.InvestigationDraft.model_validate(draft(bundle, published_contacts=[contact])), NOW)
    assert len(result.targets[0].evidence) == 2
    contact['name'] = 'Different Person'
    with pytest.raises(ValueError, match='name'):
        worker.bind_draft(subject, bundle, worker.InvestigationDraft.model_validate(draft(bundle, published_contacts=[contact])), NOW)


def test_budget_conversation_does_not_turn_aggregate_dollars_into_spend():
    r = row(description='Data recovery. Program request is $292.2 million.')
    subject = make_subject('program', r)
    bundle = worker.bundle_for(subject, sweep(r), PROFILE, NOW)
    with pytest.raises(ValueError, match='aggregate dollar'):
        worker.bind_draft(subject, bundle, worker.InvestigationDraft.model_validate(
            draft(bundle, buyer_need='A $292.2 million recovery opportunity')), NOW)



def test_disposition_history_limits_reversal_and_requires_positive_reinstatement_review(tmp_path):
    data = sweep(row())
    def run(decision, description):
        data['results']['agency_program_documents']['records'][0]['description'] = description
        return worker.run_investigations(data, PROFILE, root=tmp_path,
            synth=lambda b: draft(b, decision=decision))['items'][0]
    assert run('pursue', 'Data recovery plan A.')['state'] == 'complete'
    held = run('refresh_hold', 'Data recovery plan B, current work unknown.')
    assert held['state'] == 'complete' and held['transition']['automatic_reversals'] == 1
    cached = run('pursue', 'Data recovery plan A.')
    assert cached['state'] == 'adjudication_required' and 'overlay' not in cached
    reinstated = run('pursue', 'Data recovery plan C, new source version.')
    assert reinstated['state'] == 'adjudication_required' and 'overlay' not in reinstated
    assert 'reinstatement' in reinstated['error']
    repeated = worker.run_investigations(data, PROFILE, root=tmp_path,
        synth=lambda b: (_ for _ in ()).throw(AssertionError('no automatic adjudication retry')))
    assert repeated['calls_this_run'] == 0
    rejected = run('reject', 'Data recovery plan D, no present fit.')
    assert rejected['state'] == 'adjudication_required' and 'reversal limit' in rejected['error']
