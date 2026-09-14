"""Root-assigned Step7 repairs: cache identity, bounded collection and custody."""
import copy
import errno
import hashlib
import json
import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from tools.api import _http, sam_attachment_text as sat, sam_capture as capture
from tools.api import sam_notice_detail as detail
from tests.test_step7_evidence_collection import http_fixture, public_file

NOTICE = 'a' * 32
FOREIGN = 'f' * 32


@pytest.mark.parametrize('value', [None, [], 'bad', 7, {'sha256': []}])
@pytest.mark.parametrize('kind', ['inventory', 'text'])
def test_malformed_cache_refetches_without_notice_failure(tmp_path, monkeypatch, value, kind):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path / 'notice'))
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR', str(tmp_path / 'text'))
    if kind == 'inventory':
        calls = http_fixture(monkeypatch, b'{"attachments": []}')
        detail.fetch_notice_resources(NOTICE)
        path = tmp_path / 'notice' / f'{NOTICE}.resources.json'
        fetch = lambda: detail.fetch_notice_resources(NOTICE)
    else:
        calls = []
        def download(*a, **kw):
            calls.append(1)
            return b'Original text'
        monkeypatch.setattr(sat, '_download_bytes', download)
        sat.extract_public_attachment_text(public_file())
        path = tmp_path / 'text' / ('b' * 32 + '.json')
        fetch = lambda: sat.extract_public_attachment_text(public_file())
    saved = json.loads(path.read_text())
    saved['raw_capture'] = value
    path.write_text(json.dumps(saved))
    result = fetch()
    assert len(calls) == 2
    assert result['from_cache'] is False
    assert result['collection_status'] in ('confirmed_empty', 'captured_discovery_only')


@pytest.mark.parametrize('payload', [
    {'_links': {'self': {'href': f'https://sam.gov/api/prod/opps/v3/opportunities/{FOREIGN}/resources'}}},
    {'nested': [{'downloadUrl': f'https://sam.gov/opp/{FOREIGN}/view'}]},
    {'uri': f'/api/prod/opps/v3/opportunities/%66{FOREIGN[1:]}/resources'},
])
def test_original_notice_link_conflicts_reject(payload):
    assert not detail._resource_identity_matches(payload, NOTICE)
    assert detail._resource_identity_matches(payload, FOREIGN)
    assert detail._resource_identity_matches(
        {'href': detail.RESOURCE_DOWNLOAD_URL_TPL.format(resource_id=FOREIGN)}, NOTICE)


@pytest.mark.parametrize('final', [f'https://sam.gov/opp/{NOTICE}/view',
    f'https://sam.gov.evil.invalid/opportunities/{NOTICE}/resources',
    f'https://evil.invalid/opportunities/{NOTICE}/resources',
    f'https://sam.gov/api/prod/opps/v3/opportunities/{FOREIGN}/resources'])
def test_redirect_receipt_records_actual_origin_and_rejects_conflicts(tmp_path, monkeypatch, final):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    http_fixture(monkeypatch, b'{}')  # install the production helper, mocked transport below
    requested = detail.RESOURCES_URL_TPL.format(id=NOTICE)
    def handler(request):
        if str(request.url) == requested:
            return httpx.Response(302, headers={'location': final})
        return httpx.Response(200, json={'attachments': []})
    monkeypatch.setattr(_http, '_client', lambda timeout: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True))
    out = detail.fetch_notice_resources(NOTICE)
    assert out['raw_capture']['requested_url'] == requested
    assert out['raw_capture']['source_url'] == final
    assert out['raw_capture']['redirect_chain'] == [requested, final]
    expected = 'confirmed_empty' if final == f'https://sam.gov/opp/{NOTICE}/view' else 'identity_mismatch'
    assert out['collection_status'] == expected
    assert out['resources_checked'] is (expected == 'confirmed_empty')


@pytest.mark.parametrize('tamper', ['none', 'foreign', 'legacy', 'projection', 'naive', 'clock_type'])
def test_failed_refresh_only_returns_valid_bound_stale_inventory(tmp_path, monkeypatch, tamper):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    http_fixture(monkeypatch, b'{"attachments": []}')
    detail.fetch_notice_resources(NOTICE)
    path = tmp_path / f'{NOTICE}.resources.json'
    saved = json.loads(path.read_text())
    saved['raw_capture']['received_at'] = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    if tamper == 'foreign': saved['id'] = FOREIGN
    if tamper == 'legacy': del saved['raw_capture']
    if tamper == 'projection': saved['attachments'] = [{'name': 'invented'}]
    if tamper == 'naive': saved['raw_capture']['received_at'] = datetime.now().isoformat()
    if tamper == 'clock_type': saved['raw_capture']['received_at'] = 123
    path.write_text(json.dumps(saved))
    http_fixture(monkeypatch, b'denied', 403)
    out = detail.fetch_notice_resources(NOTICE)
    assert out['stale_cache'] is (tamper == 'none')
    if tamper == 'none':
        assert out['collection_status'] == 'stale_inventory'
        assert out['refresh_attempt']['collection_status'] == 'lookup_failed'
    else:
        assert out['attachments'] is None and out['resources_checked'] is False


@pytest.mark.parametrize('saved', [[], {'id': FOREIGN, 'description': 'Foreign requirement'},
    {'id': NOTICE, 'description': {}, 'errors': []}])
@pytest.mark.parametrize('can_refresh', [False, True])
def test_depth_cache_identity_and_structure(tmp_path, monkeypatch, saved, can_refresh):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    monkeypatch.delenv('SAM_GOV_API_KEY', raising=False)
    path = tmp_path / f'{NOTICE}.json'
    path.write_text(json.dumps(saved))
    calls = []
    monkeypatch.setattr(detail.sam_quota, 'note_call', lambda *a: None)
    def fetch(url, **kw):
        calls.append(url)
        assert can_refresh or 'resources' in url
        return {'description': 'Current matching text', 'noticeId': NOTICE} if url == detail.DESC_URL else {'attachments': []}
    from tests.test_dossiers import observed_json
    monkeypatch.setattr(detail, 'get_json', observed_json(fetch))
    out = detail.fetch_notice_depth(NOTICE, api_key='test-only-key' if can_refresh else None)
    assert out['id'] == NOTICE and out['from_cache'] is False
    assert out['description_checked'] is can_refresh
    assert len(calls) == (2 if can_refresh else 1)
    assert out['description'] == ('Current matching text' if can_refresh else None)


def test_withdrawn_history_is_retained_but_not_counted(tmp_path, monkeypatch):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    items = [dict(resourceId=c * 32, name=f'SOW {c}.txt', opportunityId=NOTICE, **flags)
             for c, flags in [('b', {}), ('c', {'deletedFlag': '1', 'deletedDate': '2026-09-01'}),
                               ('d', {'fileExists': False})]]
    payload = json.dumps({'attachments': items}).encode()
    http_fixture(monkeypatch, payload)
    result = detail.fetch_notice_resources(NOTICE)
    assert [a['resource_id'] for a in result['attachments']] == ['b' * 32]
    assert len(result['withdrawn_attachments']) == 2
    assert result['attachment_inventory_count'] == 1
    assert result['attachment_inventory_hash'] == detail._manifest_hash(result['attachments'])
    assert capture.read_retained(tmp_path, result['raw_capture']) == payload
    assert detail.fetch_notice_resources(NOTICE)['from_cache'] is True
    assert sat._public_file(result['withdrawn_attachments'][0])[0] is False


def test_saved_21_sam_inventories_preserve_identity_and_exclude_withdrawn():
    import tarfile
    proof = Path(__file__).resolve().parents[1] / 'docs/verification/step7-052/SOURCE_PROOF.tar.gz'
    recognized_count = changed_count = selected_withdrawn_count = 0
    with tarfile.open(proof) as archive:
        for member in archive.getmembers():
            if not member.name.startswith('public-inventory-001/') or not member.name.endswith('.body'):
                continue
            notice_id = Path(member.name).stem
            raw = archive.extractfile(member).read()
            metadata = json.load(archive.extractfile(member.name[:-5] + '.json'))
            assert hashlib.sha256(raw).hexdigest() == metadata['raw_sha256']
            payload = json.loads(raw)
            assert detail._resource_identity_matches(payload, notice_id)
            active, recognized = detail._attachment_inventory(payload, notice_id)
            historical = detail._withdrawn_inventory(payload, notice_id)
            recognized_count += recognized
            changed_count += bool(historical)
            old_selection = sorted(metadata.get('attachments') or [], key=sat.attachment_priority)[:3]
            selected_withdrawn_count += bool({a['resource_id'] for a in old_selection}
                                            & {a['resource_id'] for a in historical})
            assert all(not detail.attachment_withdrawn(a) for a in active or [])
            if notice_id == '0caab6e53d8b47d7850692b7684610bf':
                assert 'b9f8c1fe8b864e82affe215d0642756e' in {a['resource_id'] for a in historical}
                assert len(active) == 4
    assert (recognized_count, changed_count, selected_withdrawn_count) == (17, 4, 3)


def collect(tmp_path, monkeypatch, files, *, texts=None, clock=None, after_download=None):
    import run_searches as searches
    from agents.schemas import RawOpportunity
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR', str(tmp_path))
    monkeypatch.setattr('tools.api.sam_quota.calls_today', lambda: 0)
    monkeypatch.setattr(detail, 'fetch_notice_resources', lambda *a, **kw: {
        'id': NOTICE, 'resources_checked': True, 'attachments': files,
        'attachment_inventory_hash': detail._manifest_hash(files)})
    if clock is not None:
        monkeypatch.setattr(searches.time, 'monotonic', lambda: clock[0])
    calls = []
    def download(rid, **kw):
        calls.append(rid)
        if after_download: after_download()
        return (texts or {}).get(rid, 'Buyer requires network visibility.').encode()
    monkeypatch.setattr(sat, '_download_bytes', download)
    candidate = RawOpportunity(source='sam.gov', source_id=NOTICE, title='Requirement',
                               agency='NASA', raw_payload={'type': 'Solicitation'})
    class Source:
        last_attachment_census = {'selected': 1}
        def attachment_candidates(self, *a, **kw): return [candidate]
    rows, stats = searches._enrich_sam_public_attachments([], Source(), object(),
        CapabilityTaxonomy(client_name='Testco', version=1, updated='2026-07-21',
                           core=[TaxonomyTerm(term='network visibility')]), None, limit=8)
    return rows, stats['attachment_record_receipts'][NOTICE], calls


def test_ineligible_files_do_not_consume_slots(tmp_path, monkeypatch):
    files = [{**public_file('b'*32, 'SOW 1.txt'), 'access_level': 'private'},
             {**public_file('c'*32, 'SOW 2.txt'), 'deleted_flag': '1'},
             {**public_file('d'*32, 'SOW 3.txt'), 'size': sat.MAX_ATTACHMENT_BYTES + 1},
             public_file('e'*32, 'SOW 4.xlsx'),
             *[public_file(x*32, f'SOW {i}.txt') for i, x in enumerate('5678', 5)]]
    rows, receipt, calls = collect(tmp_path, monkeypatch, files)
    assert len(rows) == 1 and calls == [x*32 for x in '567']
    reasons = {f['resource_id']: f['reason'] for f in receipt['files']}
    assert reasons['e'*32] == 'type filter'
    assert reasons['8'*32] == 'file cap'
    assert reasons['c'*32] == 'withdrawn attachment'


def test_stage_deadline_stamps_all_remaining_files_and_preserves_acquired_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_STAGE_SECONDS', '1')
    clock = [100.0]
    def elapsed(): clock[0] = 102.0
    rows, receipt, calls = collect(tmp_path, monkeypatch,
        [public_file(x*32, f'SOW {x}.txt') for x in 'bcd'], clock=clock, after_download=elapsed)
    assert rows == [] and calls == ['b'*32]
    assert all(f['stop_reason'] == 'stage_deadline' for f in receipt['files'])
    first = receipt['files'][0]
    assert first['status'] == 'not_fetched' and first['stopped_phase'] == 'extraction'
    assert capture.read_retained(tmp_path, first['raw_capture'])


@pytest.mark.parametrize('phase', ['download', 'extraction'])
def test_nonstage_timeout_has_specific_phase(tmp_path, monkeypatch, phase):
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR', str(tmp_path))
    def timeout(*a, **kw): raise TimeoutError('bounded timeout')
    monkeypatch.setattr(sat, '_download_bytes', timeout if phase == 'download' else lambda *a, **kw: b'original')
    if phase == 'extraction': monkeypatch.setattr(sat, '_text_from_bytes', timeout)
    out = sat.extract_public_attachment_text(public_file())
    assert out['collection_status'] == 'not_fetched'
    assert out['stop_reason'] == phase + '_deadline'
    assert ('raw_capture' in out) is (phase == 'extraction')


def test_empty_bundle_tail_keeps_valid_context(tmp_path, monkeypatch):
    from agents.decisions.dossier import _attachment_research_context
    monkeypatch.setattr(sat, 'MAX_ATTACHMENT_TEXT_CHARS', 75000)
    text = 'network visibility ' + 'x' * (75000 - len('network visibility '))
    files = [public_file(x*32, f'SOW {x}.txt') for x in 'bcd']
    rows, receipt, calls = collect(tmp_path, monkeypatch, files, texts={x*32: text for x in 'bcd'})
    assert calls == ['b'*32, 'c'*32]
    row = rows[0]
    assert len(row['raw_payload']['text']) == 150000
    assert len(row['raw_payload']['attachment_collection_v1']['files']) == 2
    assert receipt['files'][2]['status'] == 'not_fetched'
    assert receipt['files'][2]['stop_reason'] == 'text_budget'
    assert _attachment_research_context(row, {'id': NOTICE,
        'attachment_inventory_hash': detail._manifest_hash(files)})['status'] == 'discovery_only'


@pytest.mark.parametrize('kind', ['inventory', 'text'])
def test_retention_failure_keeps_only_diagnostics(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR', str(tmp_path))
    def failure(*a): raise OSError('local storage unavailable')
    monkeypatch.setattr(capture, 'retain_bytes', failure)
    if kind == 'inventory':
        http_fixture(monkeypatch, b'{"attachments":[]}')
        result = detail.fetch_notice_resources(NOTICE)
        assert result['attachments'] is None and result['resources_checked'] is False
        assert result['diagnostic_inventory'] == []
    else:
        monkeypatch.setattr(sat, '_download_bytes', lambda *a, **kw: b'Acquired diagnostic text')
        result = sat.extract_public_attachment_text(public_file())
        assert result['text'] == '' and result['diagnostic_text'] == 'Acquired diagnostic text'
    assert result['collection_status'] == 'retention_failed'
    assert not list(tmp_path.glob('*.json'))


def _fallback_publisher(directory, payload):
    def unsupported(*a): raise OSError(errno.ENOTSUP, 'hardlinks unsupported')
    capture.os.link = unsupported
    receipt = capture.retain_bytes(Path(directory), payload)
    return capture.read_retained(Path(directory), receipt) == payload


def test_exclusive_create_fallback_and_conflict_quarantine(tmp_path, monkeypatch):
    payload = b'original bytes'
    receipt = capture.retain_bytes(tmp_path, payload)
    (tmp_path / receipt['path']).write_bytes(b'conflicting preserved bytes')
    def unsupported(*a): raise OSError(errno.ENOTSUP, 'hardlinks unsupported')
    monkeypatch.setattr(capture.os, 'link', unsupported)
    assert capture.retain_bytes(tmp_path, payload) == receipt
    assert capture.read_retained(tmp_path, receipt) == payload
    assert [p.read_bytes() for p in (tmp_path / 'quarantine').iterdir()] == [b'conflicting preserved bytes']


def test_fallback_concurrent_publications_are_complete(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    payload = b'large original object' * 50000
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = [pool.submit(_fallback_publisher, str(tmp_path), payload) for _ in range(8)]
        assert all(f.result(timeout=30) for f in futures)
    assert len(list((tmp_path / 'objects').iterdir())) == 1


def test_coherent_text_rewrite_is_documented_internal_consistency_limit(tmp_path, monkeypatch):
    monkeypatch.setenv('LILA_SAM_ATTACHMENT_TEXT_DIR', str(tmp_path))
    monkeypatch.setattr(sat, '_download_bytes', lambda *a, **kw: b'Original text')
    original = sat.extract_public_attachment_text(public_file())
    saved = copy.deepcopy(original)
    text = 'Coherently rewritten text, not derived from original bytes'
    saved.update(text=text, text_capture=capture.retain_bytes(tmp_path, text.encode()),
                 text_sha256=hashlib.sha256(text.encode()).hexdigest())
    saved['text_locator']['end'] = len(text)
    assert sat._text_capture_valid(saved, 'b'*32, original['attachment_binding_sha256']) is True
    assert capture.read_retained(tmp_path, saved['raw_capture']) == b'Original text'
