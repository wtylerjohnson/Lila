"""Shared next-press behavior learned from two supplied report references."""
from datetime import date
from html import unescape
import json
import pytest

from agents.leadgen.press import run_press
from agents.leadgen.press_html import render_html
from agents.golden_press.external_product_render import _lead_rows, _targets
from tests.test_research_subjects import build
from tests.test_operating_repair import _book


def reviewed_parent(monkeypatch):
    from agents.assess.reviewed_cases import ReviewedSubject
    run, _, _ = build(monkeypatch)
    receipt = run_press(assess=run, client_name='NETSCOUT')
    parent = next(p for p in receipt.parents if p.research_subject)
    subject = parent.research_subject
    research = _book().cases[0].research.model_copy(update={
        'buyer_requirement': subject.evidence[0].excerpt,
        'evidence': subject.evidence, 'priority': True,
        'rationale': 'Review this specific published requirement first.',
        'next_ask': 'Who owns this requirement and the next product decision?',
    })
    overlay = ReviewedSubject(subject_id=subject.subject_id,
                              source_sha256=subject.source_sha256, research=research)
    subject = subject.model_copy(update={'reviewed_overlay_json': overlay.model_dump_json()})
    parent = parent.model_copy(update={'research_subject': subject, 'research': research})
    receipt = receipt.model_copy(update={'parents': tuple(
        parent if p.assessment_id == parent.assessment_id else p for p in receipt.parents)})
    return receipt, parent


def test_golden_preserves_complete_current_action_and_date():
    from agents.leadgen.next_action import CurrentNextAction
    from agents.leadgen.enums import NextActionVerb
    action = CurrentNextAction(verb=list(NextActionVerb)[0],
        object='Ask the named buyer for the current kit requirements.',
        owner='Assigned operator', due=date(2026, 10, 3), blocked_by='Confirm the permitted route')
    row = {'lead_rows': [{'lead_id': 'l1', 'lead_tier': 'HOLD',
                          'next_action': action.model_dump(mode='json')}]}
    before = json.dumps(row, sort_keys=True)
    page = _lead_rows(row)
    for text in (action.object, action.owner, '2026-10-03', action.blocked_by):
        assert text in page
    assert json.dumps(row, sort_keys=True) == before


def test_golden_contact_keeps_both_channels():
    page = _targets({'targets': [{'name': 'Published person', 'role': 'Contract specialist',
        'email': 'person@example.gov', 'phone': '202-555-0104',
        'source_url': 'https://example.gov/notice/1'}]})
    assert 'person@example.gov' in page and '202-555-0104' in page
    assert 'https://example.gov/notice/1' in page
    assert 'Mobile' not in page


def test_shared_research_puts_reason_and_first_question_in_view():
    from agents.leadgen.target_html import render_research
    research = _book().cases[0].research
    page = unescape(render_research(research))
    assert research.rationale in page
    assert 'First question' in page and research.next_ask in page
    assert 'What must be confirmed' in page
    assert all(q in page for q in research.open_questions)


def test_reviewed_parent_without_children_can_be_priority(monkeypatch):
    receipt, parent = reviewed_parent(monkeypatch)
    before = receipt.model_dump_json()
    page = render_html(receipt)
    assert 'id="priority"' in page
    priority = page.split('id="priority"', 1)[1].split('id="parents"', 1)[0]
    assert parent.title in unescape(priority)
    assert 'href="#parent-' in priority
    assert not parent.lead_ids and not any(l.parent_assessment_id == parent.assessment_id for l in receipt.leads)
    assert receipt.model_dump_json() == before


def test_native_research_is_readable_action_sheet_with_full_receipts(monkeypatch):
    run, _, _ = build(monkeypatch)
    receipt = run_press(assess=run, client_name='NETSCOUT')
    page = render_html(receipt)
    assert 'Candidate opportunities and leads identified' in page
    assert '<article class="assessment-sheet"' in page
    assert 'What the buyer published' in page and 'First question' in page
    assert 'Show work: original source fields and value basis' in page
    assert 'Published forecast contacts' in page
    assert page.count('class="assessment-sheet"') == len(receipt.parents)
    assert not receipt.active_lead_t1 and not receipt.active_lead_t2


def test_unreviewed_parents_do_not_get_priority(monkeypatch):
    run, _, _ = build(monkeypatch)
    receipt = run_press(assess=run, client_name='NETSCOUT')
    assert 'id="priority"' not in render_html(receipt)


def test_new_research_questions_name_the_actual_source_without_promoting(monkeypatch):
    run, _, _ = build(monkeypatch)
    for subject in run.research.items:
        row = json.loads(subject.source_payload_json)
        if subject.source_kind == 'forecast':
            assert subject.title in subject.next_ask and 'still planned' in subject.next_ask
        else:
            assert row['piid'] in subject.next_ask and 'separate need' in subject.next_ask
        assert subject.communication_permission == 'none'


def test_detached_parent_review_cannot_create_priority(monkeypatch):
    receipt, parent = reviewed_parent(monkeypatch)
    subject = parent.research_subject.model_copy(update={'reviewed_overlay_json': None})
    detached = parent.model_copy(update={'research_subject': subject})
    receipt = receipt.model_copy(update={'parents': (detached,)})
    assert 'id="priority"' not in render_html(receipt)


def test_changed_parent_review_refuses_old_overlay(monkeypatch):
    receipt, parent = reviewed_parent(monkeypatch)
    changed = parent.model_copy(update={'research': parent.research.model_copy(update={'next_ask': 'Different ask'})})
    receipt = receipt.model_copy(update={'parents': (changed,)})
    with pytest.raises(ValueError, match='source-bound'):
        render_html(receipt)


def test_deprioritized_reason_retained_but_not_selected(monkeypatch):
    from agents.assess.reviewed_cases import ReviewedSubject
    receipt, parent = reviewed_parent(monkeypatch)
    research = parent.research.model_copy(update={'status': 'deprioritized', 'priority': False,
                                                'rationale': 'Buyer ruled out this product scope.'})
    overlay = ReviewedSubject(subject_id=parent.subject_id, source_sha256=parent.research_subject.source_sha256,
                              research=research)
    subject = parent.research_subject.model_copy(update={'reviewed_overlay_json': overlay.model_dump_json()})
    parent = parent.model_copy(update={'research_subject': subject, 'research': research})
    page = render_html(receipt.model_copy(update={'parents': (parent,)}))
    assert 'id="priority"' not in page
    assert research.rationale in page and research.next_ask in page
    assert 'Show work: original source fields' in page


def test_unknown_timing_does_not_borrow_legacy_or_print_date():
    page = _lead_rows({'lead_rows': [{'next_action': {'verb': 'research', 'object': 'Check status',
                                                    'due_at': '2099-01-01'}}]})
    assert 'Confirm timing' in page and '2099-01-01' not in page
    assert 'Communication permission' in page and 'none' in page


def test_source_sheet_escapes_and_works_for_other_clients():
    from agents.schemas import ForecastRecord
    from agents.assess.research_subjects import make_subject
    from agents.leadgen.action_sheet import research_subject_brief
    from datetime import datetime, timezone
    row = ForecastRecord(source='other_forecast', source_id='OTHER-1', agency='Other agency',
        title='Language support', description='The buyer seeks <b>interpreters</b> & translation.',
        url='https://example.gov/forecast/1', retrieved_at=datetime(2026,9,14,tzinfo=timezone.utc))
    subject = make_subject('forecast', row.model_dump(mode='json'))
    before = subject.model_dump_json()
    page = research_subject_brief(subject)
    assert '&lt;b&gt;interpreters&lt;/b&gt;' in page and '<b>interpreters</b>' not in page
    assert 'Confirm the next buying date' in page and 'NETSCOUT' not in page
    assert subject.model_dump_json() == before


def test_priority_cap_keeps_all_other_assessments_and_original_order(monkeypatch):
    from agents.assess.reviewed_cases import ReviewedSubject
    receipt, _ = reviewed_parent(monkeypatch)
    reviewed=[]
    for parent in receipt.parents:
        if parent.research_subject:
            subject=parent.research_subject
            research=_book().cases[0].research.model_copy(update={'priority':True, 'evidence':subject.evidence})
            overlay=ReviewedSubject(subject_id=subject.subject_id, source_sha256=subject.source_sha256,research=research)
            parent=parent.model_copy(update={'research_subject':subject.model_copy(update={
                'reviewed_overlay_json':overlay.model_dump_json()}),'research':research})
        reviewed.append(parent)
    receipt=receipt.model_copy(update={'parents':tuple(reviewed)})
    page=render_html(receipt)
    priority=page.split('id="priority"',1)[1].split('id="parents"',1)[0]
    assert priority.count('Open assessment, contacts and sources')==3
    assert all(f'id="parent-{i}"' in page for i in range(len(receipt.parents)))
    assert [page.index(f'id="parent-{i}"') for i in range(len(receipt.parents))]==sorted(
        page.index(f'id="parent-{i}"') for i in range(len(receipt.parents)))
    assert len(receipt.parents)==9 and not receipt.active_lead_t1 and not receipt.active_lead_t2


@pytest.mark.parametrize('source_id', ['X<img src=x onerror="alert(1)">', 'X</td><script>alert(1)</script>'])
def test_forecast_question_escapes_source_id(source_id):
    from tests.test_forecast_pocs import mapped
    from agents.leadgen.research_html import render_forecast_contact_record
    row=mapped().model_dump(mode='json')
    row.update(source='other_forecast',source_id=source_id,url='https://example.gov/forecast/1')
    page=render_forecast_contact_record(row)
    assert source_id not in page and '&lt;' in page
    assert '<img' not in page and '<script' not in page


def test_published_companies_and_contracts_are_visible_without_partner_promotion():
    from tests.test_forecast_pocs import mapped
    from tests.test_research_html_sources import award
    from agents.assess.research_subjects import make_subject
    from agents.leadgen.action_sheet import research_subject_brief
    forecast=make_subject('forecast',mapped().model_dump(mode='json'))
    page=research_subject_brief(forecast)
    assert all(t in page for t in ['ESP Corporation','70US0923F2GSA2150','GSA Schedule'])
    assert 'Company named in forecast' in page and 'does not establish a current partner commitment' in page
    award_page=research_subject_brief(award())
    assert 'TECHANAX LLC' in award_page and 'NNG15SD24B' in award_page
    assert 'Award recipient' in award_page and 'LEAD_T1' not in award_page


def test_shared_and_fallback_targets_keep_unknown_freshness_explicit():
    from agents.leadgen.target_html import render_targets
    from agents.reports.lint import lint_contact_rendering
    target=_book().cases[0].targets[0].model_dump(mode='json')
    target.update(email='person@example.gov',phone='202-555-0104',source_url='https://example.gov/person/1')
    for page in [render_targets([target]), _targets({'targets':[{k:v for k,v in target.items() if k!='next_ask'}]})]:
        result=lint_contact_rendering(page)
        assert result.ok,result.violations
        assert page.count('data-grade="C"')==2
        assert 'person@example.gov' in page and '202-555-0104' in page
        assert 'contact observation date not established' in page


@pytest.mark.parametrize('parent_award',['Not supplied as an object',['NNG15SD24B'],17,None])
def test_malformed_optional_parent_contract_preserves_source_without_crash(parent_award):
    from tests.test_research_html_sources import award
    from agents.leadgen.action_sheet import research_subject_brief
    subject=award(parent_award=parent_award)
    before=subject.source_payload_json
    page=research_subject_brief(subject)
    assert 'Award recipient' in page and '<dt>Parent contract</dt>' not in page
    assert subject.source_payload_json==before


def test_contact_bearing_source_and_reviewed_prose_keep_grade_and_provenance():
    from agents.schemas import ForecastRecord
    from agents.assess.research_subjects import make_subject
    from agents.leadgen.action_sheet import research_subject_brief
    from agents.leadgen.target_html import render_research
    from agents.reports.lint import lint_contact_rendering
    from datetime import datetime,timezone
    text='Provide packet collection. For source questions contact alice@example.gov.'
    row=ForecastRecord(source='other_forecast',source_id='OTHER-2',agency='Example',title='Packet collection',
        description=text,url='https://example.gov/forecast/2',retrieved_at=datetime(2026,9,14,tzinfo=timezone.utc))
    subject=make_subject('forecast',row.model_dump(mode='json'))
    research=_book().cases[0].research.model_copy(update={'buyer_requirement':text,'next_ask':text,'evidence':subject.evidence})
    for page in [research_subject_brief(subject), render_research(research,edit_key='test-review')]:
        result=lint_contact_rendering(page);assert result.ok,result.violations
        assert text in page and 'data-grade="C"' in page
        assert 'data-source="https://example.gov/forecast/2"' in page
