"""Step4's prespecified 39-case policy, using bounded synthetic native functions.

External retrieval is mocked; these tests do not establish authentic source or
seller success. Full mapper outputs are used, including compatibility dates.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import patch

import pytest

from agents.decisions import research_picture as rp
from agents.decisions.research_evidence import build_registry, source_record, ResearchClaim, EvidenceRef
from agents.decisions.triage import deterministic_prefilter, _latest_notice_threads
from tools.api.sam_extract import _to_opportunity, iter_rows
from tools.api.sam_gov import map_notice
from tools.relevance.notice_type import notice_type_evidence
from tools.relevance.taxonomy import load_taxonomy
from tools.relevance.temporal import source_time, record_time, clock_context

AT = datetime(2026, 9, 12, 15, tzinfo=timezone.utc)
SOW = 'The contractor shall provide supplier risk management software.'
ACTION = 'Submit proposals by September 14, 2026.'
BODY = SOW + ' ' + ACTION
ROW = dict(source_id='N1', title='Synthetic supplier procurement',
           api_url='https://sam.gov/opp/N1/view', description=BODY,
           notice_type='Solicitation', active='Yes', retrieved_at='2026-09-12T14:00:00Z',
           response_deadline='2026-09-14T17:00:00Z', naics_code='541512')


def picture(row=None, *, at=AT, passage='description', quote=None,
            action_passage=None, action_quote=None, others=(), **kwargs):
    row = deepcopy(row or ROW)
    sid = row['source_id']
    def field(name):
        return row['raw_payload'][name.split('.', 1)[1]] if name.startswith('raw_payload.') else row[name]
    text = field(passage) if quote is None else quote
    action_field = action_passage or passage
    action_text = field(action_field) if action_quote is None else action_quote
    claims = [ResearchClaim(kind=k, statement='untrusted generated text', evidence=[EvidenceRef(
        source_id=sid, passage_id=action_field if k == 'next_action' else passage,
        quote=action_text if k == 'next_action' else text)])
        for k in ('procurement_state', 'offering_fit', 'next_action')]
    draft = rp.ResearchPicture(client_name='apexanalytix', headline='', top_opportunities=[
        rp.TopOpportunity(id=sid, title='untrusted title', why_now='', claims=claims)],
        demand_signals=[], market_structure='', watchlist=[], next_action='')
    rows = [row, *deepcopy(others)]
    data = {'sam.gov': rows, 'triage': {r['source_id']: {'verdict': 'pursue'} for r in rows}}
    registry, gaps = build_registry(data, rp.distill(data))
    out = rp.validate_picture(draft, registry, gaps, at, **kwargs)
    return out, data


def assert_surfaces(out, data, expected, at=AT):
    assert out.top_opportunities[0].classification == expected
    saved = {'results': {**data, 'research_picture': out.model_dump(mode='json')}}
    projection = rp.project_saved_picture(saved, presented_at=at)
    assert projection['top'][0]['classification'] == expected
    md = rp.render_markdown(out, results=data, presented_at=at)
    assert ('Documented current notices; qualification required' in md) == (expected == 'confirmed_opportunity')
    return projection, md


@pytest.mark.parametrize('adapter', ['api', 'csv'])
@pytest.mark.parametrize('value,status,precision,normalized', [
    pytest.param('2026-09-12T12:00:01-04:00', 'aware', 'second', '2026-09-12T16:00:01+00:00', id='D01'),
    pytest.param('2026-09-11T23:30:00-02:00', 'aware', 'second', '2026-09-12T01:30:00+00:00', id='D02'),
    pytest.param('2026-09-12T16:00:00', 'unknown_timezone', 'second', None, id='D03'),
    pytest.param('2026-09-12', 'date_only', 'date', None, id='D04'),
    pytest.param(None, 'missing', None, None, id='D05'),
    pytest.param('2026-09-12 garbage', 'invalid', None, None, id='D06-suffix'),
    pytest.param('2026-09-12T99:00:00Z', 'invalid', None, None, id='D06-time'),
    pytest.param('not-a-date', 'invalid', None, None, id='D06-malformed'),
    pytest.param('2026-09-12 16:00+00:00', 'aware', 'minute', '2026-09-12T16:00:00+00:00', id='D07-minute'),
    pytest.param('2026-09-12T16:00:01.123456Z', 'aware', 'fraction', '2026-09-12T16:00:01.123456+00:00', id='D07-fraction'),
])
def test_mapper_and_registry_retain_source_time(adapter, value, status, precision, normalized):
    if adapter == 'api':
        original = dict(noticeId='N1', title='t', uiLink=ROW['api_url'], responseDeadLine=value)
        row = json.loads(map_notice(original).model_dump_json())
        field = 'raw_payload.responseDeadLine'
    else:
        original = dict(notice_id='N1', title='t', url=ROW['api_url'], deadline=value)
        row = json.loads(_to_opportunity(original).model_dump_json())
        field = 'raw_payload.deadline'
    produced = row['temporal_evidence']['deadline']
    assert produced['raw'] == value and produced['original_field'] == field
    assert produced['parse_status'] == status and produced['precision'] == precision
    assert produced['instant_utc'] == normalized
    registry = source_record('sam.gov', 'sam.gov[0]', row)
    temporal = registry.temporal_evidence['deadline']
    assert not temporal['conflict'] and temporal['status'] == status
    assert temporal['selected']['raw'] == value
    if normalized:
        assert registry.deadline == value and 'response_deadline' in temporal['derived_display_fields']
        assert produced['offset_text'] and produced['offset_minutes'] is not None


@pytest.mark.parametrize('raw', ['2026-09-12T16:00Z', '2026-09-12T16:00', '2026-09-12', '2026-09-12 garbage'])
def test_D08_csv_and_store_preserve_raw_bytes(raw):
    from tools.notice_store import shape_row, _FIELDS
    row = next(iter_rows(['NoticeId,ResponseDeadLine\n', f'N1,{raw}\n']))
    assert row['deadline'] == raw
    shaped = dict(zip(_FIELDS, shape_row(row)))
    assert shaped['deadline'] == raw


@pytest.mark.parametrize('seconds,expected,window', [
    pytest.param(-1, 'research_signal', 'closed', id='C01'),
    pytest.param(0, 'research_signal', 'closed', id='C02'),
    pytest.param(1, 'confirmed_opportunity', 'open', id='C03'),
])
def test_timestamp_boundary(seconds, expected, window):
    # Remove unrelated Sept14 prose to satisfy this case's independent prerequisites.
    out, data = picture(ROW | {'response_deadline': (AT + timedelta(seconds=seconds)).isoformat(),
                               'description': SOW + ' Submit proposals.'})
    assert_surfaces(out, data, expected)
    assert out.top_opportunities[0].temporal_status['response_window'] == window


def test_C04_equivalent_offsets_are_one_deadline():
    out, data = picture(ROW | {'raw_payload': {'deadline': '2026-09-14T13:00:00-04:00'}})
    assert_surfaces(out, data, 'confirmed_opportunity')
    assert len(out.evidence_registry['N1'].temporal_evidence['deadline']['claims']) == 2


@pytest.mark.parametrize('observation', ['2026-09-13T00:15:00Z', '2026-09-12T20:15:00-04:00'])
def test_C05_freshness_normalizes_same_instant(observation):
    at = datetime(2026, 9, 13, 0, 30, tzinfo=timezone.utc)
    out, data = picture(ROW | {'retrieved_at': observation}, at=at)
    assert_surfaces(out, data, 'confirmed_opportunity', at)


@pytest.mark.parametrize('observation,reason', [
    pytest.param(None, 'unknown_missing', id='C06'),
    pytest.param('2026-09-12T16:00Z', 'future', id='C07'),
    pytest.param('2026-09-11T14:00Z', 'stale', id='C08'),
])
def test_missing_future_stale_observation(observation, reason):
    out, data = picture(ROW | {'retrieved_at': observation})
    assert_surfaces(out, data, 'research_signal')
    assert out.top_opportunities[0].temporal_status['source_freshness'] == reason


def test_C09_trusted_historical_replay_vs_current_presentation():
    old, data = picture()
    later = AT + timedelta(days=3)
    data['research_picture'] = old.model_dump(mode='json')
    historical = rp.project_saved_picture({'results': data}, mode='historical', reference_as_of=AT, presented_at=later)
    assert historical['top'][0]['classification'] == 'confirmed_opportunity'
    assert historical['assessment_clocks']['mode'] == 'historical'
    assert historical['evidence_as_of'] == AT.isoformat()
    current = rp.project_saved_picture({'results': data}, presented_at=later)
    assert current['top'][0]['classification'] == 'research_signal'
    assert current['evidence_as_of'] == later.isoformat()
    with pytest.raises(ValueError, match='trusted caller'):
        rp.project_saved_picture({'results': data}, mode='historical', presented_at=later)
    with pytest.raises(ValueError, match='historical mode'):
        rp.project_saved_picture({'results': data}, reference_as_of=AT, presented_at=later)
    # A self-consistent saved registry and fabricated mode still cannot supply original results.
    assert rp.project_saved_picture({'results': {'research_picture': old.model_dump(mode='json')}},
                                   mode='historical', reference_as_of=AT, presented_at=later)['top'] == []
    assert 'historical assessment' in rp.render_markdown(old, results=data, mode='historical', reference_as_of=AT, presented_at=later)


@pytest.mark.parametrize('tz', ['UTC', 'America/Denver', 'Pacific/Auckland'])
def test_C10_explicit_clocks_independent_of_host_tz(monkeypatch, tz):
    import time
    monkeypatch.setenv('TZ', tz)
    try:
        time.tzset()
        out, data = picture()
        assert_surfaces(out, data, 'confirmed_opportunity')
        assert out.evidence_as_of == AT.isoformat()
        with pytest.raises(ValueError, match='aware'):
            clock_context(datetime(2026, 9, 12, 15))
    finally:
        monkeypatch.undo()
        time.tzset()


def test_C11_separate_clocks_cutoff_and_no_borrowed_observation():
    clocks = dict(query_started_at='2026-09-12T13:00Z', capture_completed_at='2026-09-12T14:15Z',
                  evidence_cutoff_at='2026-09-12T14:30Z')
    out, _ = picture(clocks=clocks)
    assert out.assessment_clocks['capture_elapsed_seconds'] == 4500
    assert out.assessment_clocks['buying_status_as_of'] == AT.isoformat()
    assert out.top_opportunities[0].classification == 'confirmed_opportunity'
    too_early, _ = picture(clocks=clocks | {'evidence_cutoff_at': '2026-09-12T13:30Z'})
    assert not too_early.top_opportunities[0].temporal_status['evidence_within_cutoff']
    assert too_early.top_opportunities[0].classification == 'research_signal'
    row = ROW | {'retrieved_at': None, 'generated_at': AT.isoformat(), 'file_mtime': AT.isoformat()}
    missing, _ = picture(row, clocks=clocks)
    assert missing.top_opportunities[0].classification == 'research_signal'
    with pytest.raises(ValueError, match='Capture cannot precede'):
        clock_context(AT, supplied=clocks | {'capture_completed_at': '2026-09-12T12:00Z'})
    with pytest.raises(ValueError, match='after presentation'):
        clock_context(AT, supplied={'evidence_cutoff_at': '2026-09-13T12:00Z'})
    with pytest.raises(ValueError, match='observation'):
        clock_context(AT, supplied={'source_observed_at': AT.isoformat()})


def test_C12_genuine_competing_deadlines_not_a_display_projection():
    out, data = picture(ROW | {'raw_payload': {'deadline': '2026-09-14T16:00Z'}})
    assert_surfaces(out, data, 'research_signal')
    assert out.evidence_registry['N1'].temporal_evidence['deadline']['conflict']
    assert any('Conflicting response deadlines' in g for g in out.gaps)


@pytest.mark.parametrize('heading', ['Archived', 'Historical', 'Previous', 'Prior'])
@pytest.mark.parametrize('narrow', [False, True])
def test_A01_A02_A03_old_instruction_heading_survives_narrow_citation(heading, narrow):
    text = SOW + f' {heading} instructions: ' + ACTION
    out, data = picture(ROW | {'description': text}, action_quote='Submit proposals' if narrow else text)
    assert_surfaces(out, data, 'research_signal')
    assert not out.top_opportunities[0].action_evidence[0]['supported']


@pytest.mark.parametrize('text,quote', [
    (SOW + ' Current instructions: ' + ACTION, 'Submit proposals'),
    (SOW + ' Current instructions: ' + ACTION, SOW + ' Current instructions: ' + ACTION),
    (SOW + ' Archived instructions: Submit proposals by September 1, 2025. Current instructions: Send responses by September 14, 2026.', 'Send responses'),
])
def test_A04_current_and_mixed_current_positive(text, quote):
    out, data = picture(ROW | {'description': text}, action_quote=quote)
    assert_surfaces(out, data, 'confirmed_opportunity')


def test_A04_duplicate_narrow_quote_is_ambiguous():
    text = SOW + ' Archived instructions: Submit proposals by September 1, 2025. Current instructions: ' + ACTION
    out, data = picture(ROW | {'description': text}, action_quote='Submit proposals')
    assert_surfaces(out, data, 'research_signal')


@pytest.mark.parametrize('prose,deadline', [
    pytest.param('2026-09-12T12:00Z', '2026-09-12T17:00Z', id='A05'),
    pytest.param('September 15, 2026', ROW['response_deadline'], id='A06'),
])
def test_A05_A06_prose_deadline_conflict(prose, deadline):
    out, data = picture(ROW | {'description': SOW + ' Submit proposals by ' + prose + '.', 'response_deadline': deadline})
    assert_surfaces(out, data, 'research_signal')
    assert any('conflict' in g for c in out.top_opportunities[0].action_evidence[0]['clauses'] for g in c['gaps'])


def mapped(sid, posted, body=BODY, deadline=ROW['response_deadline']):
    row = json.loads(_to_opportunity(dict(notice_id=sid, solicitation='SYN-049', agency='SYN-AGENCY',
             office='SYN-OFFICE', title='Synthetic procurement', description=body, type='Solicitation',
             active='Yes', naics='541512', posted=posted, deadline=deadline,
             retrieved_at=ROW['retrieved_at'], url=f'https://sam.gov/opp/{sid}/view')).model_dump_json())
    return row


@pytest.mark.parametrize('day', ['2026-09-11', '2026-09-12'], ids=['M01', 'M02'])
def test_same_day_and_next_day_withdrawal_suppress_older_positive(day):
    rows = [mapped('Z-OLD', '2026-09-11T09:00Z'), mapped('A-NEW', day + 'T16:00Z',
            'This procurement is withdrawn. Supplier risk management software is not required.')]
    selected, superseded, count = _latest_notice_threads(rows)
    assert selected[0]['source_id'] == 'A-NEW'
    assert superseded['Z-OLD']['superseded_by'] == 'A-NEW' and count == 1
    assert len(selected[0]['raw_payload']['triage_thread_members']) == 2
    candidates, ruled, _ = deterministic_prefilter(rows, load_taxonomy('apexanalytix'))
    assert candidates == [] and ruled['Z-OLD']['superseded_by'] == 'A-NEW'
    old, data = picture(rows[0], passage='raw_payload.description_snippet', others=[rows[1]])
    assert_surfaces(old, data, 'research_signal')


def test_M03_current_extension_binds_only_new_notice():
    old = mapped('N0', '2026-09-10T09:00Z', deadline='2026-09-11T17:00Z')
    new = mapped('N1', '2026-09-12T13:00Z', SOW + ' Amended instructions: ' + ACTION)
    out, data = picture(new, passage='raw_payload.description_snippet', others=[old])
    assert_surfaces(out, data, 'confirmed_opportunity')
    out, data = picture(old, passage='raw_payload.description_snippet', others=[new])
    assert_surfaces(out, data, 'research_signal')


def test_M04_expired_context_and_independently_bound_continuation():
    expired = ROW | {'description': SOW + ' Contact the office to request a briefing.', 'response_deadline': '2026-09-11T17:00Z'}
    current = ROW | {'source_id': 'N2', 'api_url': 'https://sam.gov/opp/N2/view'}
    out, data = picture(expired, others=[current])
    assert_surfaces(out, data, 'research_signal')
    assert out.top_opportunities[0].temporal_status['response_window'] == 'closed'
    out, data = picture(current, others=[expired])
    assert_surfaces(out, data, 'confirmed_opportunity')


@pytest.mark.parametrize('old_time,new_time', [
    ('2026-09-11', '2026-09-11'), ('2026-09-11T09:00', '2026-09-11T16:00'),
    ('2026-09-11T16:00Z', '2026-09-11T12:00-04:00'), (None, None),
    ('2026-09-11', '2026-09-11T16:00Z'),
])
def test_M05_uncertain_posting_order_does_not_resurrect_old_support(old_time, new_time):
    rows = [mapped('Z-OLD', old_time), mapped('A-NEW', new_time, 'Supplier risk management software is not required.')]
    selected, dispositions, count = _latest_notice_threads(rows)
    assert selected[0]['raw_payload']['triage_thread_order_status'] == 'unresolved'
    assert count == 0
    assert all(v['superseded_by'] is None for v in dispositions.values())
    candidates, ruled, receipt = deterministic_prefilter(rows, load_taxonomy('apexanalytix'))
    assert not candidates and ruled['Z-OLD']['verdict'] == 'unscreened'
    out, data = picture(rows[0], passage='raw_payload.description_snippet', others=[rows[1]])
    assert_surfaces(out, data, 'research_signal')


@pytest.mark.parametrize('label', ['Award Notice', 'Award', 'A', 'Awards', 'Award Notification', 'Awarded', 'Award Synopsis', 'Awarded Contract'])
def test_T01_T02_attributed_explicit_award_labels(label):
    row = ROW | {'notice_type': None, 'raw_payload': {'type': label, 'baseType': 'Solicitation'}}
    assert notice_type_evidence(row)['historical']
    assert not deterministic_prefilter([row], load_taxonomy('apexanalytix'))[0]
    out, data = picture(row)
    assert_surfaces(out, data, 'research_signal')


@pytest.mark.parametrize('label', ['Justification', 'Justification and Approval', 'J&A', 'Unknown', 'Preaward', 'Pre-Award', 'Presolicitation', 'Solicitation', 'Sources Sought'])
def test_T03_T04_unknown_justification_preaward_and_current_types(label):
    row = ROW | {'notice_type': label}
    assert not notice_type_evidence(row)['historical']
    assert deterministic_prefilter([row], load_taxonomy('apexanalytix'))[0]
    out, data = picture(row)
    expected = 'confirmed_opportunity' if label in {'Solicitation', 'Sources Sought'} else 'research_signal'
    assert_surfaces(out, data, expected)


@pytest.mark.parametrize('mode,passage,expected', [
    pytest.param('discovery', 'raw_payload.text', 'research_signal', id='P01'),
    pytest.param('missing_clock', 'raw_payload.text', 'research_signal', id='P02'),
    pytest.param('mixed', 'raw_payload.description_snippet', 'confirmed_opportunity', id='P03'),
    pytest.param('mixed', 'raw_payload.text', 'research_signal', id='P04'),
])
def test_native_enrichment_authority_through_every_projection(monkeypatch, mode, passage, expected):
    from tools import env
    from tools.api import sam_quota, sam_notice_detail, sam_attachment_text
    with patch.object(env, 'load_env', return_value=None):
        import run_searches as rs
    candidate = _to_opportunity(dict(notice_id='N1', title='Synthetic procurement', url=ROW['api_url'],
        type='Solicitation', active='Yes', naics='541512', deadline=ROW['response_deadline'],
        description=BODY if mode == 'mixed' else 'Administrative notice. ' + ACTION,
        retrieved_at=None if mode == 'missing_clock' else ROW['retrieved_at']))
    class Source:
        last_attachment_census = {'synthetic': True}
        def attachment_candidates(self, *args, **kwargs):
            return [candidate]
    attachment = dict(resource_id='synthetic-SOW', name='SOW.txt', source_url=ROW['api_url'])
    extracted = attachment | dict(text=BODY, sha256=hashlib.sha256(BODY.encode()).hexdigest(), retrieved_at=ROW['retrieved_at'])
    monkeypatch.setattr(sam_quota, 'calls_today', lambda *a, **k: 0)
    monkeypatch.setattr(sam_notice_detail, 'fetch_notice_resources', lambda *a, **k: dict(resources_checked=True, attachments=[attachment], attachment_inventory_hash='synthetic-inventory'))
    monkeypatch.setattr(sam_attachment_text, 'extract_public_attachment_text', lambda *a, **k: extracted)
    taxonomy = load_taxonomy('apexanalytix')
    rows, receipt = rs._enrich_sam_public_attachments([json.loads(candidate.model_dump_json())], Source(), object(), taxonomy, None, limit=1)
    assert receipt['attachment_enriched'] == 1
    assert deterministic_prefilter(rows, taxonomy)[0]  # investigation remains possible
    out, data = picture(rows[0], passage=passage)
    assert_surfaces(out, data, expected)
    source = out.evidence_registry['N1']
    assert source.passage_authority['raw_payload.text']['authority'] == 'discovery_only'
    assert source.attachment_provenance['attachment_evidence']
    assert source.passage_authority['raw_payload.description_snippet']['authority'] == 'original_notice'
    if passage == 'raw_payload.text':
        assert 'not requirement approval' in out.top_opportunities[0].why_now
        tampered = out.model_copy(deep=True)
        tampered.evidence_registry['N1'].passage_authority['raw_payload.text']['authority'] = 'original_notice'
        tampered.assessment_clocks = {'mode': 'historical'}
        rebound = rp.revalidate_picture(tampered, data, presented_at=AT)
        assert rebound.top_opportunities[0].classification == 'research_signal'
        stripped = deepcopy(rows[0]); stripped['raw_payload'].pop('attachment_evidence', None)
        stripped['raw_payload']['passage_authority'] = {'text': 'original_notice'}
        missing, _ = picture(stripped, passage=passage)
        assert missing.top_opportunities[0].classification == 'research_signal'


@pytest.mark.parametrize('adapter,passage', [('api', 'raw_payload.description'), ('csv', 'raw_payload.description_snippet')])
def test_D01_full_native_mapper_projection_uses_exact_instant(adapter, passage):
    body = SOW + ' Submit proposals by 2026-09-12T16:00:01Z.'
    if adapter == 'api':
        row = json.loads(map_notice(dict(noticeId='N1', title='t', uiLink=ROW['api_url'],
            responseDeadLine='2026-09-12T12:00:01-04:00', description=body,
            type='Solicitation', active='Yes', retrieved_at=ROW['retrieved_at'])).model_dump_json())
    else:
        row = json.loads(_to_opportunity(dict(notice_id='N1', title='t', url=ROW['api_url'],
            deadline='2026-09-12T12:00:01-04:00', description=body,
            type='Solicitation', active='Yes', retrieved_at=ROW['retrieved_at'])).model_dump_json())
    out, data = picture(row, passage=passage)
    assert_surfaces(out, data, 'confirmed_opportunity')
    assert out.top_opportunities[0].deadline == '2026-09-12T12:00:01-04:00'
