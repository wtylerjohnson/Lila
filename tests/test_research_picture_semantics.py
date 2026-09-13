"""Step3 typed-claim extension; source binding from Step1 remains required."""
from copy import deepcopy
import pytest
from tests.test_research_picture_evidence import NOTICE, AS_OF, results, claim, picture, validate, TopOpportunity


def render_case(text, quote=None, **change):
    row={**deepcopy(NOTICE), 'description':text, **change}
    claims=[claim(kind=k,quote=quote or text) for k in ('procurement_state','offering_fit','next_action')]
    out=validate(picture(client_name='apexanalytix',top_opportunities=[TopOpportunity(id='N1',title='draft',why_now='draft',claims=claims)]),results(row))
    return out.top_opportunities[0]


@pytest.mark.parametrize('text', [
    'Evaluation uses SPRS supplier risk management information. Submit a response tomorrow.',
    'No supplier risk management software is required. Submit a response tomorrow.',
    'Supplier risk management. Submit a response tomorrow.',
    'The contractor shall provide supplier risk management software.',
    'The contractor shall provide supplier risk management software. Do not submit a response.',
    'The contractor shall provide supplier risk management software. Correction: No supplier risk management is required. Submit a response.',
    'The contractor shall provide supplier risk management software. Historical instructions: Submit proposals by September 14, 2020.',
    'The contractor shall provide supplier risk management software. Submit proposals by September 1, 2026.',
    'The contractor shall provide supplier risk management software. Historical instructions:\nSubmit proposals by September 14, 2026.',
])
def test_relabeling_quotes_does_not_supply_missing_predicates(text):
    assert render_case(text).classification=='research_signal'


def test_real_request_grammar_and_response_action_stay_bounded():
    text='The contractor shall provide supplier risk management software and monthly reports. Submit a response by September 14.'
    assert render_case(text).classification=='confirmed_opportunity'
    assert render_case(text,quote='supplier risk management').classification=='research_signal'
    assert render_case(text,notice_type='Awarded').classification=='research_signal'


def test_unavailable_current_taxonomy_cannot_confirm(monkeypatch):
    import tools.relevance.taxonomy as module
    monkeypatch.setattr(module,'load_taxonomy',lambda _:None)
    monkeypatch.setattr(module,'derived_taxonomy',lambda _:None)
    assert render_case('The contractor shall provide supplier risk management software. Submit a response.').classification=='research_signal'


def test_action_quote_cannot_strip_historical_context_or_borrow_uncited_current_action():
    from agents.decisions.research_evidence import _current_response_instruction
    text='Historical instructions:\nSubmit proposals by September 14, 2026.'
    assert not _current_response_instruction(text,AS_OF,'Submit proposals by September 14, 2026.')
    text+='\nCurrent instruction: Submit questions by September 14, 2026.'
    assert not _current_response_instruction(text,AS_OF,'Submit proposals by September 14, 2026.')
    assert _current_response_instruction(text,AS_OF,'Submit questions by September 14, 2026.')
