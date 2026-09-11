"""Adversarial tests of evidence promotion, independent of any live model."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from agents.decisions.research_evidence import (
    EvidenceRef, ResearchClaim, build_registry, checked_claim, current_notice,
)
from agents.decisions.research_picture import (
    ResearchPicture, TopOpportunity, compose_research_picture, distill,
    render_markdown, validate_picture,
)
from agents.reports.capture_brief import quarantine_web_assertions

AS_OF = datetime(2026, 9, 11, 15, tzinfo=timezone.utc)
BODY = 'The agency seeks supplier bank account validation. Submit a response by September 14.'
NOTICE = {
    'source_id': 'N1', 'title': 'Supplier validation research',
    'api_url': 'https://sam.gov/opp/N1/view',
    'description': BODY, 'notice_type': 'Sources Sought', 'active': 'Yes',
    'retrieved_at': '2026-09-11T14:00:00+00:00',
    'response_deadline': '2026-09-14T12:00:00-04:00',
}


def results(row=None):
    return {'sam.gov': [deepcopy(NOTICE if row is None else row)],
            'triage': {'N1': {'verdict': 'pursue'}}}


def claim(kind='offering_fit', sid='N1', passage='description', quote=BODY, **kwargs):
    return ResearchClaim(kind=kind, statement='Unsupported paraphrase of a perfect match',
                         evidence=[EvidenceRef(source_id=sid, passage_id=passage, quote=quote)], **kwargs)


def picture(**kwargs):
    data = dict(client_name='Apex', headline='Two live solicitations are exact matches.',
                top_opportunities=[], demand_signals=['Guaranteed live demand'],
                market_structure='Incumbent must recompete tomorrow', watchlist=[],
                next_action='Bid now with guaranteed access.', gaps=[])
    data.update(kwargs)
    return ResearchPicture(**data)


def validate(draft, data):
    registry, gaps = build_registry(data, distill(data))
    return validate_picture(draft, registry, gaps, AS_OF)


def test_title_url_only_cannot_become_active_or_exact_fit():
    data = {'web': [{'source_id': 'W1', 'title': 'VA PIVOT', 'url': 'https://sam.gov/opp/x/view'}]}
    draft = picture(top_opportunities=[TopOpportunity(id='W1', title='Active solicitation',
                       why_now='Exact functional match. Bid now.', deadline='2099-01-01')])
    out = validate(draft, data)
    item = out.top_opportunities[0]
    assert item.classification == 'research_signal'
    assert item.deadline is None and item.title == 'VA PIVOT'
    assert item.validation_status == 'VERIFICATION_REQUIRED'
    assert 'Verify original notice' in render_markdown(out)
    for unsupported in ['Exact functional match', 'Two live solicitations', 'Bid now', 'recompete tomorrow']:
        assert unsupported not in out.model_dump_json()


def test_unknown_ids_are_rejected_without_title_matching_or_signal_loss():
    data = {'web': [{'source_id': 'web-canonical', 'title': 'VA PIVOT', 'url': 'https://sam.gov/x'}]}
    out = validate(picture(top_opportunities=[TopOpportunity(id='36C10B26Q0072',
                        title='VA PIVOT', why_now='Live')]), data)
    assert not out.top_opportunities
    assert out.research_signals[0].id == 'web-canonical'
    assert any('unknown source ID 36C10B26Q0072' in x for x in out.validation_issues)


@pytest.mark.parametrize('sid,passage,quote', [('invented','description',BODY),
    ('N1','invented',BODY), ('N1','description','The VA guarantees an award')])
def test_bad_reference_is_rejected(sid, passage, quote):
    registry, _ = build_registry(results(), distill(results()))
    accepted, reason = checked_claim(claim(sid=sid, passage=passage, quote=quote), registry)
    assert accepted is None and reason


def test_valid_quote_does_not_validate_fabricated_paraphrase():
    out = validate(picture(narrative_claims=[claim()]), results())
    assert 'Unsupported paraphrase' not in out.headline
    assert BODY in out.narrative_claims[0].statement
    assert '[N1/description]' in out.headline


@pytest.mark.parametrize('kind', ['procurement_state', 'value', 'incumbent', 'vehicle_route'])
def test_inference_cannot_fill_missing_procurement_facts(kind):
    out = validate(picture(narrative_claims=[claim(kind=kind, basis='inference')]), results())
    assert not out.narrative_claims
    assert any('requires source facts' in x for x in out.validation_issues)


def test_cross_source_inference_is_explicit_and_never_qualifies():
    out = validate(picture(narrative_claims=[claim(basis='inference')]), results())
    assert 'Inference (requires verification)' in out.headline
    assert not out.top_opportunities


def test_current_original_notice_requires_evidence_and_stays_unqualified():
    claims = [claim(kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')]
    data = results()
    out = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='invented',
                                       why_now='invented', claims=claims)]), data)
    item = out.top_opportunities[0]
    assert item.classification == 'confirmed_opportunity'
    assert item.validation_status == 'EXCERPTS_CHECKED_NOT_QUALIFIED'
    assert 'existing qualification' in item.why_now
    assert data == results()  # no mutation of source, triage, or approval inputs


@pytest.mark.parametrize('change', [
    {'active': 'No'}, {'notice_type': 'Award Notice'},
    {'retrieved_at': None}, {'retrieved_at': '2026-09-10T14:00:00Z'},
    {'retrieved_at': '2026-09-12T14:00:00Z'}, {'response_deadline': None},
    {'response_deadline': '2026-09-11'}, {'response_deadline': '2026-09-11T15:00:00Z'},
    {'api_url': 'https://sam.gov.evil.example/x'}, {'description': ''},
])
def test_unknown_stale_closed_or_secondary_notice_stays_signal(change):
    row = {**NOTICE, **change}
    out = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w',
                        claims=[claim(kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')])]), results(row))
    assert out.top_opportunities[0].classification == 'research_signal'


def test_web_government_url_with_rich_text_cannot_impersonate_original_retrieval():
    out = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w',
                        claims=[claim(kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')])]),
                   {'web': [NOTICE]})
    assert out.top_opportunities[0].classification == 'research_signal'
    assert 'Discovery excerpt (unverified)' in out.top_opportunities[0].why_now


def test_award_expiry_remains_historical_without_recompete_claim():
    data = {'contract_awards': {'recompetes': [{'source_id': 'A1', 'title': 'Old award',
                                               'completion': '2026-09-13'}]}}
    out = validate(picture(top_opportunities=[TopOpportunity(id='A1', title='Recompete', why_now='Bid now')]), data)
    assert out.top_opportunities[0].classification == 'historical_market_evidence'
    assert 'Bid now' not in render_markdown(out)


def test_other_notices_cannot_supply_this_notices_required_claims():
    data = results()
    data['sam.gov'].append({**NOTICE, 'source_id': 'N2'})
    data['triage']['N2'] = {'verdict': 'pursue'}
    out = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w',
                    claims=[claim(sid='N2', kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')])]), data)
    assert out.top_opportunities[0].classification == 'research_signal'
    assert not out.top_opportunities[0].claims


def test_source_failure_and_noncomprehensive_coverage_survive_to_markdown():
    data = {'source_coverage_verdict': {'comprehensive': False, 'blocking_sources': ['congress'],
             'lanes': [{'source': 'congress', 'required': True, 'state': 'failed'}]},
            '_attempts': [{'source': 'congress', 'ok': False}],
            'contract_awards': {'error': 'missing credentials'}}
    md = render_markdown(validate(picture(), data))
    assert 'not comprehensive' in md and 'Required source congress: failed' in md
    assert 'Source contract_awards: retrieval failed' in md


def test_web_passages_remain_inside_existing_compose_quarantine():
    data = {'web': [{'source_id': 'W', 'title': 'Web title', 'raw_payload': {'summary': 'WEB CLAIM'}}]}
    digest = distill(data)
    assert digest['web_leads'][0]['passages']['raw_payload.summary'] == 'WEB CLAIM'
    cleaned, _ = quarantine_web_assertions({'research_picture': validate(picture(), data).model_dump(),
                                           'source_sweep': digest})
    assert 'WEB CLAIM' not in str(cleaned)
    assert 'evidence_registry' not in cleaned['source_sweep']


def test_conflicting_canonical_ids_fail_closed():
    data = {'web': [{'source_id': 'W1', 'title': 'first'}, {'source_id': 'W1', 'title': 'second'}]}
    registry, gaps = build_registry(data, distill(data))
    assert 'W1' not in registry
    assert any('Ambiguous source ID W1' in g for g in gaps)


def test_source_passages_are_bounded_and_raw_input_not_mutated():
    data = {'web': [{'source_id': 'W1', 'title': 't', 'raw_payload': {'summary': 'x' * 5000}}]}
    before = deepcopy(data)
    registry, gaps = build_registry(data, distill(data))
    assert len(registry['W1'].passages['raw_payload.summary']) == 2000
    assert registry['W1'].truncated and gaps
    assert data == before


def test_composer_ignores_model_generated_registry_and_validation_status():
    class Engine:
        def deliberate(self, **kwargs):
            assert 'evidence_registry' in kwargs['context']
            return picture(validation_version='research-picture.evidence.v1',
                top_opportunities=[TopOpportunity(id='invented', title='active', why_now='bid',
                                   classification='confirmed_opportunity')])
    out = compose_research_picture('Apex', 'supplier validation', results(), engine=Engine())
    assert not out.top_opportunities
    assert 'Two live solicitations' not in render_markdown(out)


def test_legacy_render_fails_closed():
    md = render_markdown(picture())
    assert 'Two live solicitations' not in md and 'Bid now' not in md
    assert 'Legacy picture has no validated evidence registry' in md


def test_saved_case005_va_claims_are_research_tasks_with_all_required_gaps():
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/research_picture_case005.json').read_text())
    out = validate(ResearchPicture.model_validate(fixture['draft']), fixture['results'])
    assert not out.top_opportunities
    assert {s.id for s in out.research_signals} == {'web-af425e4fb2f913ce', 'web-b0d1a4671b98953d'}
    assert len([x for x in out.validation_issues if 'unknown source ID' in x]) == 2
    assert all(any(f'Required source {lane}:' in g for g in out.gaps)
               for lane in fixture['results']['source_coverage_verdict']['blocking_sources'])
    md = render_markdown(out)
    assert 'exact functional match' not in md.lower()
    assert 'Verify original notice identity' in md


def test_total_registry_budget_and_native_order_are_disclosed():
    data = {f'lane{i}': [{'source_id': f'{i}-{j}', 'title': 't', 'description': 'x'*1800}
                        for j in range(15)] for i in range(20)}
    registry, gaps = build_registry(data, distill(data))
    assert len(registry) <= 80
    assert sum(len(s.model_dump_json()) for s in registry.values()) <= 120000
    assert list(registry)[:3] == ['0-0', '0-1', '0-2']
    assert any('budget reached' in g for g in gaps)


@pytest.mark.parametrize('change', [
    {'api_url': 'https://sam.gov/opp/N2/view'},
    {'active': 'No', 'raw_payload': {'active': 'Yes'}},
    {'raw_payload': {'type': 'Award Notice'}},
])
def test_identity_and_conflicting_status_cannot_confirm_notice(change):
    out = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w',
        claims=[claim(kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')])]),
        results({**NOTICE, **change}))
    assert out.top_opportunities[0].classification == 'research_signal'


def test_composer_binds_client_and_focus_to_operator_input_without_filtering():
    class Engine:
        def deliberate(self, **kwargs):
            return picture(client_name='Wrong client')
    out = compose_research_picture('Apex', 'supplier validation', results(), engine=Engine(),
                                  operator_focus={'agencies': [{'abbr': 'DHS'}], 'mode': 'focus'})
    assert out.client_name == 'Apex'
    assert out.headline.startswith('Engagement focus: DHS.')
    assert 'N1' in out.evidence_registry


@pytest.mark.parametrize('change', [
    {'retrieval_status': 'failed'}, {'raw_payload': {'retrieval_status': 'unreadable'}},
    {'retrieval_status': 'ok', 'raw_payload': {'retrieval_status': 'failed'}},
])
def test_explicit_failed_source_cannot_support_facts(change):
    out = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w',
        claims=[claim(kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')])]),
        results({**NOTICE, **change}))
    assert out.top_opportunities[0].classification == 'research_signal'
    assert not out.top_opportunities[0].claims
    assert out.evidence_registry['N1'].retrieval_status == 'failed'
    assert any('retrieval state failed' in g for g in out.gaps)


@pytest.mark.parametrize('state', ['discovery_only', 'unknown', 'stale', 'metadata_only'])
def test_non_authoritative_retrieval_state_survives_normalization(state):
    out = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w',
        claims=[claim(kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')])]),
        results({**NOTICE, 'retrieval_status': state}))
    assert out.top_opportunities[0].classification == 'research_signal'
    assert out.evidence_registry['N1'].retrieval_status == state


def test_version_marker_cannot_bypass_markdown_claim_validation():
    out = validate(picture(), results())
    out.headline = 'Invented live opportunity'
    out.next_action = 'Bid now'
    md = render_markdown(out)
    assert 'Invented live opportunity' not in md and 'Bid now' not in md
