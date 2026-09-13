"""Step1 recheck046: persisted assertions cannot authenticate their own sources."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from agents.decisions import research_picture as rp
from agents.decisions.research_evidence import EvidenceRef, checked_claim
from tests.test_research_picture_evidence import (
    AS_OF, BODY, NOTICE, claim, picture, results, validate, TopOpportunity,
)


@pytest.fixture(autouse=True)
def current_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AS_OF if tz else AS_OF.replace(tzinfo=None)
    monkeypatch.setattr(rp, 'datetime', Clock)


def checked_picture(data=None):
    return validate(picture(top_opportunities=[TopOpportunity(
        id='N1', title='discarded model title', why_now='discarded model reason',
        claims=[claim(kind=k) for k in ('procurement_state', 'offering_fit', 'next_action')])]),
        data if data is not None else results())


@pytest.mark.parametrize('version', ['research-picture.evidence.v1', 'research-picture.evidence.v2'])
def test_forged_saved_registry_fails_closed_in_native_projection_and_markdown(version):
    from tests.test_research_picture_ui import project
    saved = checked_picture().model_dump(mode='json')
    saved['validation_version'] = version
    saved['evidence_registry']['N1'].update(
        title='INVENTED OPPORTUNITY', deadline='2099-01-01T00:00:00Z',
        primary_notice=True, retrieval_status='stored_source_text')
    wrapper = {'results': {'research_picture': saved}}
    before = deepcopy(wrapper)
    view = project(wrapper)
    assert not view['top'] and not view['research_cards']
    assert 'INVENTED OPPORTUNITY' not in json.dumps(view)
    assert 'registry cannot validate itself' in str(view['gaps'])
    md = rp.render_markdown(rp.ResearchPicture.model_validate(saved))
    assert 'INVENTED OPPORTUNITY' not in md and '2099' not in md
    assert wrapper == before


def test_independent_source_rebuild_overrides_saved_registry_and_fields():
    saved = checked_picture().model_dump(mode='json')
    saved['evidence_registry']['N1'].update(title='INVENTED', deadline='2099-01-01T00:00:00Z')
    out = rp.project_saved_picture({'results': {**results(), 'research_picture': saved}})
    assert out['top'][0]['title'] == NOTICE['title']
    assert out['top'][0]['deadline'] == NOTICE['response_deadline']
    assert out['top'][0]['classification'] == 'confirmed_opportunity'
    assert 'INVENTED' not in json.dumps(out)


@pytest.mark.parametrize('quote', [' ', '\t\n', '...', '---'])
def test_empty_or_punctuation_quote_cannot_confirm_current_notice(quote):
    claims = [claim(kind=k, quote=quote) for k in ('procurement_state', 'offering_fit', 'next_action')]
    p = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w', claims=claims)]), results())
    assert p.top_opportunities[0].classification == 'research_signal'
    assert not p.top_opportunities[0].claims
    assert any('non-substantive' in issue for issue in p.validation_issues)


def test_meaningful_other_notice_route_cannot_become_own_channel_fact():
    data = results()
    route = 'Award access is through the other named prime on Contract B.'
    data['sam.gov'].append({**NOTICE, 'source_id': 'N2', 'description': route})
    data['triage']['N2'] = {'verdict': 'monitor'}
    c = claim(kind='vehicle_route')
    c.evidence.append(EvidenceRef(source_id='N2', passage_id='description', quote=route))
    p = validate(picture(top_opportunities=[TopOpportunity(id='N1', title='t', why_now='w', claims=[c])]), data)
    assert p.top_opportunities[0].classification == 'research_signal'
    assert not p.top_opportunities[0].claims
    assert 'other named prime' not in p.top_opportunities[0].why_now


@pytest.mark.parametrize('raw', [
    {'response_deadline': '2020-01-01T00:00:00Z'},
    {'deadline': '2099-01-01T00:00:00Z'},
    {'notice_id': 'N2'},
])
def test_conflicting_primary_metadata_prevents_current_confirmation(raw):
    p = checked_picture(results({**NOTICE, 'raw_payload': raw}))
    assert p.top_opportunities[0].classification == 'research_signal'
    assert p.evidence_registry['N1'].binding_issues
    if 'notice_id' not in raw:
        assert p.top_opportunities[0].deadline is None


def test_equivalent_timezone_deadlines_are_not_a_conflict():
    same = datetime.fromisoformat(NOTICE['response_deadline']).astimezone(AS_OF.tzinfo).isoformat()
    p = checked_picture(results({**NOTICE, 'raw_payload': {'deadline': same}}))
    assert not p.evidence_registry['N1'].binding_issues
    assert p.top_opportunities[0].classification == 'confirmed_opportunity'


def test_saved_clock_cannot_reactivate_stale_source():
    old = AS_OF - timedelta(days=4)
    data = results({**NOTICE, 'retrieved_at': old.isoformat()})
    saved = checked_picture(data).model_dump(mode='json')
    saved['evidence_as_of'] = old.isoformat()
    out = rp.project_saved_picture({'results': {**data, 'research_picture': saved}})
    assert out['top'][0]['classification'] == 'research_signal'
    assert out['evidence_as_of'] == AS_OF.isoformat()


def test_changed_source_passage_invalidates_saved_claims_without_mutating_inputs():
    saved = checked_picture().model_dump(mode='json')
    data = {'results': {**results({**NOTICE, 'description': 'Different original requirement.'}),
                        'research_picture': saved}}
    before = deepcopy(data)
    out = rp.project_saved_picture(data)
    assert out['top'][0]['classification'] == 'research_signal'
    assert not out['top'][0]['claims']
    assert data == before


def test_owned_markdown_path_keeps_source_backed_content():
    p = checked_picture()
    md = rp.render_markdown(p, results=results())
    assert NOTICE['title'] in md and BODY in md
    assert 'Documented current notices' in md


def test_existing_picture_runner_passes_owning_sources_to_markdown(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    import run_picture
    import agents.review
    import agents.assess.ledger
    path = tmp_path / 'sweep.json'
    path.write_text(json.dumps({'results': results()}))
    monkeypatch.setattr(run_picture, '__file__', str(tmp_path / 'run_picture.py'))
    monkeypatch.setattr(agents.review, 'load_approved', lambda _: SimpleNamespace(pursuit_strategy='supplier controls'))
    monkeypatch.setattr(agents.review, 'sweep_artifact_path', lambda _: str(path))
    monkeypatch.setattr(agents.assess.ledger, 'materialize_current_assess_run', lambda *a, **k: None)
    monkeypatch.setattr(rp, 'compose_research_picture', lambda *a, **k: checked_picture())
    monkeypatch.setattr(sys, 'argv', ['run_picture.py', '--client', 'Apex', '--skip', 'triage'])
    assert run_picture.main() == 0
    md = (tmp_path / 'data/review/apex.research_picture.md').read_text()
    assert NOTICE['title'] in md and BODY in md
    assert 'registry cannot validate itself' not in md
