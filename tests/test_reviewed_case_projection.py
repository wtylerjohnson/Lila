"""BENCH-004: retained research cannot invalidate the discovered SAM lane."""
import copy
import hashlib

import pytest

from agents.assess.ledger import AssessLedgerError, assess_projection_input_manifest, build_assess_run
from agents.assess.live_report import LiveReportState, project_live_ledger, resolve_current_live_report
from agents.assess.reviewed_cases import load_cases, save_cases
from agents.reports.document import build_document
from agents.reports.views import render_assessment
from tests.test_live_report_truth import _cutover_sweep, _seed_runtime, _persist
from tests.test_operating_repair import _book


def _seed(tmp_path, monkeypatch):
    searches = _cutover_sweep()
    seed = _seed_runtime(tmp_path, monkeypatch, searches)
    book = _book()
    save_cases(book, seed['review_dir'])
    inputs = assess_projection_input_manifest('Testco', review_dir=seed['review_dir'])
    run, diagnostics, index = build_assess_run(
        'Testco', searches, seed['profile'], seed['binding'],
        approval_payload=seed['approval'], approval_status='approved',
        requirement_reviews_payload=seed['requirements'],
        projection_inputs=inputs, reviewed_cases=book,
    )
    seed.update(run=run, diagnostics=diagnostics, posting_index=index,
                projection_inputs=inputs, searches=searches, book=book)
    _persist(seed)
    return seed


def _resolve(seed):
    return resolve_current_live_report(
        'Testco', seed['searches'], seed['profile'],
        state_dir=seed['state_dir'], review_dir=seed['review_dir'],
        effective_date=seed['run'].as_of.date(),
    )


def test_bound_reviewed_research_renders_without_fake_discovery(tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    raw_before = copy.deepcopy(seed['searches'])
    projection = _resolve(seed)
    assert projection.state == LiveReportState.CURRENT, projection.problem
    assert len(projection.reviewed_research) == 3
    assert {c.record.notice_id for c in projection.reviewed_research} == {
        c.record.notice_id for c in seed['book'].cases}
    assert sum(projection.verdict_counts().values()) == len(seed['posting_index'])
    assert len(projection.notices) + len(projection.reviewed_research) == len(seed['run'].live.records)
    for case in projection.reviewed_research:
        assert case in seed['book'].cases
        assert case.record.notice_id not in seed['posting_index']
        assert case.record.notice_id not in {n.record.notice_id for n in projection.actionable}
    document = build_document('Testco', searches=seed['searches'],
                              as_of=seed['run'].as_of.date(),
                              _live_report=projection, _cap_profile=seed['profile'])
    rows = [e for e in document.watchlist.entries if e.source == 'reviewed_case']
    assert len(rows) == 3
    assert all(e.kind == 'standing' for e in rows)
    html = render_assessment(document, 'client')
    for case in projection.reviewed_research:
        assert case.record.title in html
        assert str(case.record.authoritative_evidence[-1].source_url) in html
        assert case.record.response_deadline.isoformat() in html
    assert 'Reviewed research:' in html
    assert seed['searches'] == raw_before


@pytest.mark.parametrize('mutation', ['absent_book', 'changed_record', 'fake_index', 'raw_missing_index'])
def test_unbound_records_and_forged_sweep_associations_still_refuse(tmp_path, monkeypatch, mutation):
    seed = _seed(tmp_path, monkeypatch)
    book = seed['book']
    index = dict(seed['posting_index'])
    searches = copy.deepcopy(seed['searches'])
    if mutation == 'absent_book':
        book = None
    elif mutation == 'changed_record':
        case = book.cases[0]
        book = book.model_copy(update={'cases': (
            case.model_copy(update={'record': case.record.model_copy(update={'title': 'Forged title'})}),
            *book.cases[1:])})
    elif mutation == 'fake_index':
        index[book.cases[0].record.notice_id] = book.cases[0].record.record_id
    else:
        raw = searches['results']['sam.gov'][0]
        index.pop(raw['source_id'])
    with pytest.raises(AssessLedgerError):
        project_live_ledger(seed['run'].live, index, searches, reviewed_cases=book)


@pytest.mark.parametrize('mutation', ['scope', 'client'])
def test_casebook_cannot_cross_client_or_scope(tmp_path, monkeypatch, mutation):
    seed = _seed(tmp_path, monkeypatch)
    update = {'scope_designator': 'agency_dhs'} if mutation == 'scope' else {'client_name': 'Otherco'}
    with pytest.raises(AssessLedgerError):
        project_live_ledger(seed['run'].live, seed['posting_index'], seed['searches'],
                            reviewed_cases=seed['book'].model_copy(update=update))


def test_casebook_drift_invalidates_current_projection(tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    path = seed['review_dir'] / 'testco.reviewed_cases.json'
    raw = path.read_bytes()
    old_hash = hashlib.sha256(raw).hexdigest()
    path.write_bytes(raw + b'\n')
    projection = _resolve(seed)
    assert projection.state == LiveReportState.INVALID
    assert 'inputs have changed' in projection.problem
    # A replacement between the manifest check and the source read also fails.
    with pytest.raises(ValueError, match='changed'):
        load_cases('Testco', seed['review_dir'], expected_sha256=old_hash)
    path.unlink()
    with pytest.raises(ValueError, match='missing'):
        load_cases('Testco', seed['review_dir'], expected_sha256=old_hash)


def test_removed_priority_keeps_history_without_a_target_reason(tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    book = seed['book']
    case = book.cases[0]
    book = book.model_copy(update={'cases': (
        case.model_copy(update={'research': case.research.model_copy(update={
            'status': 'rejected', 'priority': False})}), *book.cases[1:])})
    projection = project_live_ledger(seed['run'].live, seed['posting_index'], seed['searches'],
                                     reviewed_cases=book)
    assert len(projection.reviewed_research) == 3
    document = build_document('Testco', searches=seed['searches'],
                              as_of=seed['run'].as_of.date(),
                              _live_report=projection, _cap_profile=seed['profile'])
    assert case.record.notice_id not in {e.id for e in document.watchlist.entries}


def test_read_time_casebook_failure_returns_invalid(tmp_path, monkeypatch):
    seed = _seed(tmp_path, monkeypatch)
    import agents.assess.live_report as owner
    def unreadable(*args, **kwargs):
        raise PermissionError('casebook became unreadable')
    monkeypatch.setattr(owner, 'load_cases', unreadable)
    projection = _resolve(seed)
    assert projection.state == LiveReportState.INVALID
    assert 'casebook became unreadable' in projection.problem


def test_reviewed_priorities_cannot_displace_the_native_watchlist(tmp_path, monkeypatch):
    from agents.reports.document import _build_watchlist
    from agents.assess.live_report import LiveReportProjection
    from agents.assess.contracts import LiveRecommendation
    from types import SimpleNamespace
    seed = _seed(tmp_path, monkeypatch)
    # Forty existing displayed monitor reasons must survive adding research.
    native = tuple(SimpleNamespace(
        record=SimpleNamespace(notice_id=f'M{i}', recommendation=LiveRecommendation.MONITOR,
                               classification=seed['book'].cases[0].record.classification),
        current={'source_id': f'M{i}', 'title': f'Monitor {i}', 'api_url': f'https://sam.gov/opp/M{i}/view'},
    ) for i in range(40))
    projection = LiveReportProjection(state=LiveReportState.CURRENT, client_name='Testco',
        scope_designator='all', notices=native, reviewed_research=seed['book'].cases)
    watchlist = _build_watchlist({}, 'testco', None, seed['run'].as_of.date(), live_report=projection)
    assert [e.id for e in watchlist.entries[:40]] == [f'M{i}' for i in range(40)]
    assert len(watchlist.entries) == 43
    assert all(e.source == 'reviewed_case' for e in watchlist.entries[40:])


def test_research_link_binds_its_notice_when_evidence_also_cites_other_postings(tmp_path, monkeypatch):
    from agents.assess.reviewed_cases import ReviewedCase
    from agents.assess.live_report import LiveReportProjection
    from agents.reports.document import _build_watchlist
    seed = _seed(tmp_path, monkeypatch)
    case = seed['book'].cases[0]
    payload = case.model_dump(mode='json')
    extra = dict(payload['record']['authoritative_evidence'][0])
    extra.update(evidence_id='other-notice', source_url='https://sam.gov/opp/other-notice/view')
    payload['record']['authoritative_evidence'].append(extra)
    reviewed = ReviewedCase.model_validate(payload)
    projection = LiveReportProjection(state=LiveReportState.CURRENT, client_name='Testco',
        scope_designator='all', reviewed_research=(reviewed,))
    watchlist = _build_watchlist({}, 'testco', None, seed['run'].as_of.date(), live_report=projection)
    assert watchlist.entries[0].url == str(case.record.authoritative_evidence[-1].source_url)
