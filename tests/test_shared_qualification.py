"""055: material values and seller promotion need their actual source meaning.

Synthetic counterexamples preserve the meeting's failure modes without asserting
that the meeting action sheet was produced by these native code paths.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from agents.golden_press.external_product_projection import _graph_record
from agents.leadgen.press import run_press
from agents.leadgen.enums import LeadTier
from tests.test_leadgen_qualify import (
    _run, _holder_partner, _decision_actions, _prime_partner, _t1_target_actions,
)


@pytest.mark.parametrize('obligations', [3100000, 0])
def test_award_value_keeps_obligations_and_ceiling_separate(obligations):
    row = _graph_record({
        'record_id': 'EXAMPLE-AWARD',
        'source_url': 'https://www.usaspending.gov/award/CONT_AWD_EXAMPLE_7001_NONE_NONE',
        'obligated_dollars': obligations, 'ceiling_dollars': 4400000,
        'period_start': '2024-04-01', 'period_end': '2027-04-01',
    }, kind='award')
    values = {item['measure']: item for item in row.get('value_evidence', [])}
    assert set(values) == {'cumulative_obligations', 'contract_ceiling'}
    assert values['cumulative_obligations']['amount'] == obligations
    assert values['contract_ceiling']['amount'] == 4400000
    assert all(item['source_url'] == row['source_url'] for item in values.values())
    assert all(item['period'] and item['explanation'] for item in values.values())
    assert 'annual' not in values


def test_unbound_renewal_action_wording_cannot_promote():
    receipt = run_press(assess=_run(partners=[_holder_partner()]),
                        target_actions=_decision_actions())
    assert receipt.parents and receipt.leads
    assert all(row.lead_tier not in {LeadTier.LEAD_T1, LeadTier.LEAD_T2}
               for row in receipt.leads)
    assert any('decision' in (row.next_action.blocked_by or '').lower()
               for row in receipt.leads)


def test_unbound_prime_role_wording_cannot_promote():
    actions = _t1_target_actions()
    actions['rows'][0].update(row_class='displacement', rule_id='T2',
        why_this_account='Prime-led recompete; vendor component role is packet capture.')
    receipt = run_press(assess=_run(partners=[_prime_partner()]), target_actions=actions)
    assert receipt.parents and receipt.leads
    assert all(row.lead_tier not in {LeadTier.LEAD_T1, LeadTier.LEAD_T2}
               for row in receipt.leads)
    assert any('tool' in (row.next_action.blocked_by or '').lower()
               or 'source' in (row.next_action.blocked_by or '').lower()
               for row in receipt.leads)


def _expansion_overlay():
    from agents.assess.reviewed_cases import ReviewedSubject
    from agents.leadgen.targets import LeadResearch
    from agents.assess.research_subjects import make_subject
    from tests.test_research_subjects import AWARDS
    raw = AWARDS[0]['data']
    subject = make_subject('award', raw)
    research = LeadResearch(status='known_opportunity', rationale='Existing business context',
        buyer_requirement='Source describes existing maintenance',
        fit_hypothesis='Current installed scope requires reconciliation',
        route='Recorded award holder; future route remains a question',
        why_now='Customer reports the prior renewal completed',
        next_ask='Reconcile the completed renewal against funding actions',
        open_questions=('Annual value and next authorized buying window',),
        reviewed_by='synthetic reviewer', reviewed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        evidence=subject.evidence)
    investigation = {
        'investigation_id': 'expansion:separate-operations-unit',
        'scope': 'Separate operations unit', 'purpose': 'expansion',
        'research': {**research.model_dump(mode='json'), 'status': 'investigation',
            'next_ask': 'Which tools does this unit own, and who may select and fund replacements?'},
        'customer_statements': [{
            'statement_id': 'meeting:scope-1', 'speaker': 'Customer participant',
            'source_document_sha256': 'a' * 64, 'transcript_timestamp': '06:29',
            'source_excerpt': 'We serve one unit. We meet the other unit next week.',
            'quote': 'We serve one unit. We meet the other unit next week.',
            'topic': 'installed_base', 'certainty': 'reported',
            'event_date': None, 'relative_date_text': 'next week',
        }],
    }
    overlay = ReviewedSubject(subject_id=subject.subject_id,
        source_sha256=subject.source_sha256, research=research,
        investigations=[investigation])
    return subject, overlay


def test_expansion_is_separate_from_award_and_relative_date_never_rolls():
    subject, overlay = _expansion_overlay()
    assert overlay.research.status == 'known_opportunity'
    assert overlay.investigations[0].research.status == 'investigation'
    assert overlay.investigations[0].customer_statements[0].event_date is None
    assert overlay.investigations[0].customer_statements[0].relative_date_text == 'next week'
    assert overlay.investigations[0].scope != subject.title


@pytest.mark.parametrize('sentence', [
    'The renewal decision on 2026-10-01 was completed.',
    'No renewal decision is scheduled for 2026-10-01.',
    'The renewal decision on 2026-10-01 was canceled.',
    'The renewal decision may occur on 2026-10-01.',
    'Is there a renewal decision on 2026-10-01?',
    'A renewal decision occurred on 2026-04-01.',
    'A renewal decision occurs on 2027-10-01.',
    'The period of performance ends on 2026-10-01.',
    'An unrelated award decision occurs on 2026-10-01.',
])
def test_decision_meaning_not_action_wording_controls_promotion(sentence):
    from tests.test_leadgen_qualify import _timed_live, _evidence
    from agents.assess.contracts import EvidenceUse
    excerpt = 'The contractor shall provide packet capture. Example Reseller is the incumbent. ' + sentence
    live = _timed_live().model_copy(update={'requirement_excerpt': excerpt,
        'authoritative_evidence': (_evidence(excerpt=excerpt, supports=[EvidenceUse.REQUIREMENT, EvidenceUse.TIMING]),)})
    receipt = run_press(assess=_run(live=[live], partners=[_holder_partner()]), target_actions=_decision_actions())
    assert receipt.parents and receipt.leads
    assert not receipt.active_lead_t1 and not receipt.active_lead_t2


@pytest.mark.parametrize('date_text', ['2026-10-01', '10/01/2026', 'October 1, 2026', '1 Oct 2026'])
def test_real_future_renewal_preserves_common_source_date_forms(date_text):
    from tests.test_leadgen_qualify import _timed_live, _evidence
    from agents.assess.contracts import EvidenceUse
    live = _timed_live()
    excerpt = live.requirement_excerpt.replace('2026-10-01', date_text)
    live = live.model_copy(update={'requirement_excerpt': excerpt,
        'authoritative_evidence': (_evidence(excerpt=excerpt, supports=[EvidenceUse.REQUIREMENT, EvidenceUse.TIMING]),)})
    receipt = run_press(assess=_run(live=[live], partners=[_holder_partner()]), target_actions=_decision_actions())
    assert len(receipt.active_lead_t1) == 1


@pytest.mark.parametrize('authority', [
    'The prime shall operate government-furnished packet capture software.',
    'The prime may select and supply packet capture software.',
    'The prime shall not select and supply packet capture software.',
    'Will the prime select and supply packet capture software?',
    'The prime shall design a software architecture.',
    'The prime shall operate the current competitor software.',
    'The prime shall select and supply packet capture software. The government-furnished software must be used.',
])
def test_services_competitor_or_optional_tool_role_stays_research(authority):
    from tests.test_leadgen_qualify import _timed_live, _evidence
    from agents.assess.contracts import EvidenceUse
    excerpt = ('The contractor shall provide packet capture. '
               'The recompete award decision is on 2026-10-01. '
               'Example Reseller is an eligible prime for this recompete. ' + authority)
    live = _timed_live().model_copy(update={'requirement_excerpt': excerpt,
        'authoritative_evidence': (_evidence(excerpt=excerpt, supports=[EvidenceUse.REQUIREMENT, EvidenceUse.TIMING]),)})
    for row_class in ('renewal', 'displacement'):
        actions = _decision_actions()
        actions['rows'][0].update(row_class=row_class, rule_id='T2',
                                  why_this_account='Prime-led recompete; vendor component role is packet capture.')
        receipt = run_press(assess=_run(live=[live], partners=[_prime_partner()]), target_actions=actions)
        assert receipt.parents and receipt.leads
        assert not receipt.active_lead_t1 and not receipt.active_lead_t2


@pytest.mark.parametrize('edit', ['other_source', 'secondary', 'unknown_clock', 'other_incumbent'])
def test_current_decision_must_belong_to_primary_subject_and_holder(edit):
    from tests.test_leadgen_qualify import _timed_live
    live = _timed_live()
    e = live.authoritative_evidence[0]
    if edit == 'other_source': e = e.model_copy(update={'source_url': 'https://sam.gov/opp/OTHER/view'})
    if edit == 'secondary': e = e.model_copy(update={'primary_source': False})
    if edit == 'unknown_clock': e = e.model_copy(update={'source_acquisition': None, 'retrieved_at': None})
    if edit == 'other_incumbent': e = e.model_copy(update={'excerpt': e.excerpt.replace('Example Reseller', 'Different Company')})
    live = live.model_copy(update={'authoritative_evidence': (e,), 'requirement_excerpt': e.excerpt})
    if edit != 'other_incumbent':
        with pytest.raises(ValueError):
            _run(live=[live], partners=[_holder_partner()])
        return
    receipt = run_press(assess=_run(live=[live], partners=[_holder_partner()]), target_actions=_decision_actions())
    assert not receipt.active_lead_t1 and not receipt.active_lead_t2


def test_original_award_and_action_have_separate_amount_meaning():
    from agents.reports.value_evidence import value_evidence, render_values
    from tests.test_research_subjects import AWARDS
    raw = AWARDS[0]['data']
    values = value_evidence(raw, source_url=AWARDS[0]['sourceURL'])
    by_measure = {v['measure']: v for v in values}
    assert by_measure['cumulative_obligations']['amount'] == 3126654.64
    assert by_measure['contract_ceiling']['amount'] == 4371483.8
    assert by_measure['contract_ceiling']['period']['end'].startswith('2028-04-15')
    assert by_measure['cumulative_obligations']['period']['end'] == '2027-04-15'
    assert 'annual_renewal' not in by_measure
    assert 'not an annual renewal' in render_values(values)
    action = value_evidence({'federal_action_obligation': 0, 'action_date': '2026-04-09'})[0]
    assert action['measure'] == 'funding_action' and action['amount'] == 0
    assert action['period']['action_date'] == '2026-04-09'


def test_other_client_values_keep_annual_estimate_negative_action_and_unknown_separate():
    from agents.reports.value_evidence import value_evidence
    for source, measure, value in [
        ({'annual_renewal_amount': 80000, 'renewal_year': 2027}, 'annual_renewal', 80000),
        ({'estimated_value_range': '$2M to $5M', 'fiscal_year': 2027}, 'future_estimate', '$2M to $5M'),
        ({'federal_action_obligation': -1000}, 'funding_action', -1000),
        ({'published_value': 91000}, 'unspecified_published_value', 91000),
    ]:
        item = value_evidence(source | {'record_id': 'OTHER-CLIENT'})[0]
        assert (item['measure'], item['amount']) == (measure, value)
        assert item['period'] and item['source_field'] and item['explanation']


def test_context_renders_retains_history_and_never_creates_a_lead(monkeypatch, tmp_path):
    import json
    from agents.assess.reviewed_cases import ReviewedCases, save_cases, load_cases
    from agents.assess.research_subjects import apply_reviewed_subjects
    from agents.leadgen.press import render_html, render_markdown
    from tests.test_research_subjects import build
    run, _, _ = build(monkeypatch)
    subject, overlay = _expansion_overlay()
    parent = next(s for s in run.research.items if s.subject_id == subject.subject_id)
    # Bind the actual enriched ledger bytes, including acquisition metadata.
    # Raw API fixture hashes are not interchangeable with this source input.
    overlay = overlay.model_copy(update={'source_sha256': parent.source_sha256})
    book = ReviewedCases(schema_version='reviewed_cases.v2', client_name='NETSCOUT', subjects=(overlay,))
    assert parent.source_sha256 == overlay.source_sha256
    save_cases(book, tmp_path)
    retained = load_cases('NETSCOUT', tmp_path)
    updated = run.model_copy(update={'research': apply_reviewed_subjects(run.research, retained, 'all')})
    receipt = run_press(assess=updated, reviewed_cases=retained)
    p = next(p for p in receipt.parents if p.subject_id == subject.subject_id)
    assert not p.lead_ids and p.research.status == 'known_opportunity'
    for report in (render_html(receipt), render_markdown(receipt)):
        assert 'Separate operations unit' in report
        assert 'next week' in report and 'Meeting date not established' in report
        assert 'Which tools does this unit own' in report
    assert p.research_subject.source_payload_json == parent.source_payload_json
    second = overlay.model_copy(update={'research': overlay.research.model_copy(update={'status': 'deprioritized'})})
    save_cases(book.model_copy(update={'subjects': (second,)}), tmp_path)
    history = list((tmp_path / 'research_history' / 'netscout').glob('*.json'))
    assert len(history) == 2
    assert {json.loads(p.read_text())['subjects'][0]['research']['status'] for p in history} == {'known_opportunity', 'deprioritized'}


def test_customer_context_rejects_unbound_quote_duplicate_ids_and_preserves_legacy():
    from agents.assess.reviewed_cases import ReviewedSubject
    _, overlay = _expansion_overlay()
    data = overlay.model_dump(mode='json')
    data['investigations'][0]['customer_statements'][0]['quote'] = 'The NOC has approved a budget.'
    with pytest.raises(ValueError): ReviewedSubject.model_validate(data)
    with pytest.raises(ValueError): ReviewedSubject.model_validate(overlay.model_dump(mode='json') | {'investigations': [overlay.investigations[0]] * 2})
    legacy = ReviewedSubject(subject_id=overlay.subject_id, source_sha256=overlay.source_sha256, research=overlay.research)
    assert 'customer_statements' not in legacy.model_dump(mode='json')
    assert 'investigations' not in legacy.model_dump(mode='json')


def test_another_client_brand_award_is_retained_as_research_only(monkeypatch):
    from tests.test_research_subjects import research_sweep, NOW
    from tests.test_assess_ledger import BINDING
    from agents.assess.ledger import build_assess_run
    sweep, profile = research_sweep(monkeypatch)
    profile = profile.model_copy(update={'client_name': 'Otherco'})
    sweep['client'] = 'Otherco'
    records = sweep['results']['incumbent_buyer_map']['research_award_details']['records']
    for record in records:
        record['description'] = record['description'].replace('NETSCOUT', 'OTHERCO')
    run, _, _ = build_assess_run('Otherco', sweep, profile, BINDING, as_of=NOW)
    awards = [s for s in run.research.items if s.source_kind == 'award']
    assert awards, 'Same source/brand rule must apply to every client'
    receipt = run_press(assess=run)
    assert all(not p.lead_ids for p in receipt.parents if p.research_subject is not None)


def test_four_original_forecasts_keep_identity_value_dates_and_research_boundary(monkeypatch):
    import json
    from pathlib import Path
    from tools.api.forecasts.dhs_apfs import map_record
    from agents.assess.research_subjects import make_subject
    from agents.reports.value_evidence import value_evidence
    from agents.leadgen.research_html import render_source_fields
    raw = json.loads((Path(__file__).parent / 'fixtures/shared_qualification_apfs.json').read_text())['records']
    subjects = {r['id']: make_subject('forecast', map_record(r).model_dump(mode='json')) for r in raw}
    assert len({s.subject_id for s in subjects.values()}) == 4
    for sid, title, amount, award in [
        (70335, 'Architecture', '$50M to $100M', '2027-02-25'),
        (70136, 'Operations', '$50M to $100M', '2026-12-16'),
        (74410, 'Gigamon', '$2M to $5M', '2027-09-15'),
        (74411, 'CoreLight', '$1M to $2M', '2027-09-03'),
    ]:
        subject = subjects[sid]
        payload = json.loads(subject.source_payload_json)
        assert title.casefold() in subject.title.casefold()
        values = value_evidence(payload, source_url=str(subject.source_url), source_id=subject.source_record_id)
        estimate = next(v for v in values if v['measure'] == 'future_estimate')
        assert estimate['amount'] == amount
        assert datetime.strptime(estimate['period']['planned_award'], '%m/%d/%Y').date().isoformat() == award
        assert subject.source_posture == 'forecast_plan' and subject.status == 'research_needed'
        assert subject.communication_permission == 'none'
        html = render_source_fields(subject, as_of=datetime(2026, 9, 14).date())
        assert amount in html and 'Estimated future value' in html and 'Show work' in html
    assert 'Architecture' not in subjects[70136].title
    assert '2026-09-15' not in value_evidence(json.loads(subjects[74410].source_payload_json))[0]['period'].values()


def test_withholding_current_notice_also_withholds_saved_amount_explanations():
    from agents.golden_press.external_product_projection import _withhold_notice
    row = _graph_record({'record_id': 'N1', 'ceiling_dollars': 900000}, kind='notice')
    _withhold_notice(row, ['Current source not available'])
    assert row['value'] is None and row['value_evidence'] == []
    assert row['saved_assessment']['value_evidence'][0]['amount'] == 900000


ROOT_PROBES = json.loads((Path(__file__).parent / 'fixtures/shared_qualification_root_probes.json').read_text())['probes']
ROOT_PROBES += json.loads((Path(__file__).parent / 'fixtures/shared_qualification_root_adjacent.json').read_text())['probes']
ROOT_PROBES += json.loads((Path(__file__).parent / 'fixtures/shared_qualification_root_multi.json').read_text())['probes']


@pytest.mark.parametrize('probe', ROOT_PROBES, ids=lambda p: p['probe'])
def test_independent_root_semantic_probes(probe):
    from tests.test_leadgen_qualify import _timed_live, _evidence
    from agents.assess.contracts import EvidenceUse
    text = probe['source_excerpt']
    live = _timed_live().model_copy(update={'requirement_excerpt': text,
        'authoritative_evidence': (_evidence(excerpt=text, supports=[EvidenceUse.REQUIREMENT, EvidenceUse.TIMING]),)})
    prime = probe['probe'].startswith(('prime_', 'multi_prime_'))
    actions = _decision_actions()
    if prime: actions['rows'][0].update(row_class='displacement', rule_id='T2', why_this_account='Prime-led recompete; vendor component role is packet capture.')
    receipt = run_press(assess=_run(live=[live], partners=[_prime_partner() if prime else _holder_partner()]), target_actions=actions)
    assert bool(receipt.active_lead_t1 or receipt.active_lead_t2) == (probe['expected'] == 'ACTIVE_LEAD')
    assert receipt.parents and receipt.leads


def test_root_reduced_buyer_map_keeps_obligation_basis_and_end():
    from agents.reports.value_evidence import value_evidence
    native = {'kind': 'award', 'amount': 3126654.64, 'amount_basis': 'obligated_to_date',
              'end_date': '2027-04-15', 'url': 'https://www.usaspending.gov/award/CONT_AWD_EXAMPLE'}
    values = value_evidence(native)
    assert len(values) == 1
    assert values[0]['measure'] == 'cumulative_obligations'
    assert values[0]['amount'] == 3126654.64
    assert values[0]['period']['end'] == '2027-04-15'
    assert values[0]['source_field'] == '/amount' and values[0]['source_basis_field'] == '/amount_basis'
    assert values[0]['source_url'] == native['url']


@pytest.mark.parametrize('role,event', [
    ('Example Reseller is the incumbent.', 'The office-equipment renewal decision is on 2026-10-01.'),
    ('Example Reseller is the incumbent office-equipment contractor.', 'The seat faces a renewal decision on 2026-10-01.'),
    ('Example Reseller is the incumbent for an office-equipment contract.', 'The seat faces a renewal decision on 2026-10-01.'),
])
def test_incumbent_and_event_cannot_borrow_another_requirement(role,event):
    from tests.test_leadgen_qualify import _timed_live, _evidence
    from agents.assess.contracts import EvidenceUse
    text = 'The contractor shall provide packet capture. ' + role + ' ' + event
    live = _timed_live().model_copy(update={'requirement_excerpt':text,
        'authoritative_evidence':(_evidence(excerpt=text,supports=[EvidenceUse.REQUIREMENT,EvidenceUse.TIMING]),)})
    receipt=run_press(assess=_run(live=[live],partners=[_holder_partner()]),target_actions=_decision_actions())
    assert not receipt.active_lead_t1 and not receipt.active_lead_t2


def test_generic_software_is_not_a_concrete_requirement_for_renewal_promotion():
    from tests.test_leadgen_qualify import _timed_live, _evidence
    from agents.assess.contracts import EvidenceUse
    text=_timed_live().requirement_excerpt.replace('packet capture','software')
    live=_timed_live().model_copy(update={'requirement_excerpt':text,
        'authoritative_evidence':(_evidence(excerpt=text,supports=[EvidenceUse.REQUIREMENT,EvidenceUse.TIMING]),)})
    receipt=run_press(assess=_run(live=[live],partners=[_holder_partner()]),target_actions=_decision_actions())
    assert not receipt.active_lead_t1 and not receipt.active_lead_t2


def test_repeated_same_requirement_does_not_create_a_second_scope():
    from tests.test_leadgen_qualify import _timed_live, _evidence
    from agents.assess.contracts import EvidenceUse
    text='The contractor must provide packet capture. ' + _timed_live().requirement_excerpt
    live=_timed_live().model_copy(update={'requirement_excerpt':text,
        'authoritative_evidence':(_evidence(excerpt=text,supports=[EvidenceUse.REQUIREMENT,EvidenceUse.TIMING]),)})
    receipt=run_press(assess=_run(live=[live],partners=[_holder_partner()]),target_actions=_decision_actions())
    assert receipt.active_lead_t1
