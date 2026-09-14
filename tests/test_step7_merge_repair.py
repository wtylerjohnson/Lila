"""054: real acquisition to native Assess/LeadRow, legacy migration and custody."""
import errno
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from tools.api import _http, sam_notice_detail as nd, sam_attachment_text as sat, sam_capture
from tools.artifacts import atomic_write_json
from tests.test_step7_evidence_collection import http_fixture, public_file

A, B, FILE = 'a' * 32, 'b' * 32, 'c' * 32
REQUIREMENT = 'The contractor shall provide an enterprise packet capture platform for CISA network operations.'


def transport(monkeypatch, handler):
    http_fixture(monkeypatch, b'{}')
    monkeypatch.setattr(_http, '_client', lambda timeout: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True))
    monkeypatch.setattr(nd.sam_quota, 'note_call', lambda *a: None)


@pytest.mark.parametrize('leg', ['description', 'resources'])
@pytest.mark.parametrize('target', ['same', 'foreign', 'offhost', 'idless', 'nonnotice_named', 'http'])
def test_depth_both_legs_retained_origin_and_native_assess(tmp_path, monkeypatch, leg, target):
    from tests.test_assess_ledger import NOW, _sweep, _build, _build_with_requirement_review
    from agents.leadgen.from_assess import draft_lead_rows
    from run_dossiers import _source_depth_record
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(nd, 'datetime', Clock)
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    origin = nd._description_url(A) if leg == 'description' else nd.RESOURCES_URL_TPL.format(id=A)
    finals = {'same': origin + '&r=1' if leg == 'description' else origin + '?r=1',
              'foreign': origin.replace(A, B), 'offhost': origin.replace('sam.gov', 'evil.invalid'),
              'idless': 'https://sam.gov/login', 'nonnotice_named': f'https://sam.gov/login?noticeid={A}',
              'http': origin.replace('https:', 'http:')}
    calls = []
    def handler(request):
        url = nd._public_capture_url(request.url)
        calls.append(url)
        if url == origin:
            return httpx.Response(302, headers={'location': finals[target]})
        if 'noticedesc' in str(request.url) or (leg == 'description' and url == finals[target]):
            return httpx.Response(200, json={'description': REQUIREMENT})
        return httpx.Response(200, json={'attachments': []})
    transport(monkeypatch, handler)
    depth = nd.fetch_notice_depth(A, api_key='SECRET_TEST_KEY')
    allowed = target == 'same'
    assert depth[leg + '_checked'] is allowed
    capture = depth[leg + '_capture']
    assert sam_capture.read_retained(tmp_path, capture)
    assert 'SECRET_TEST_KEY' not in json.dumps(depth)
    assert capture['source_url'] == finals[target]
    sweep = json.loads(json.dumps(_sweep()).replace('N1', A))
    projected = _source_depth_record(A, f'https://sam.gov/opp/{A}/view', depth)
    sweep['results']['dossiers'] = {'depth_records': [{'id': A, 'source_depth': projected}]}
    if allowed:
        run, _, _ = _build_with_requirement_review(sweep, notice_id=A)
        record = next(r for r in run.live.records if r.notice_id == A)
        assert record.classification.value == 'bid_now'
        assert len(draft_lead_rows(run).leads) == 1
        assert record.requirement_excerpt == REQUIREMENT
    else:
        run, _, _ = _build(sweep)
        record = next(r for r in run.live.records if r.notice_id == A)
        assert record.classification.value != 'bid_now'
        assert record.requirement_reviewed_at is None
        assert len(draft_lead_rows(run).leads) == 0
        if leg == 'description':
            assert projected['description'] is None
            assert not (tmp_path / f'{A}.json').exists()


def base_written_depth(notice, body):
    """Use the original writer/projection, not a lifecycle-enriched approximation."""
    import types
    source = subprocess.check_output(['git', 'show', '67f644b:tools/api/sam_notice_detail.py'], text=True)
    module = types.ModuleType('base_depth_054')
    module.__file__ = nd.__file__
    exec(compile(source, 'base_depth_054', 'exec'), module.__dict__)
    module.get_json = lambda url, **kw: {'description': REQUIREMENT} if 'noticedesc' in url else body
    return module.fetch_notice_depth(notice, api_key='MOCK_KEY')


@pytest.mark.parametrize('refresh', ['recognized', 'unknown', 'failed'])
def test_base_written_trusted_looking_depth_never_grandfathered(tmp_path, monkeypatch, refresh):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    monkeypatch.delenv('SAM_GOV_API_KEY', raising=False)
    body = {'attachments': [{'resourceId': FILE, 'name': 'Withdrawn SOW.pdf', 'deletedFlag': '1'}]}
    old = base_written_depth(A, body)
    path = tmp_path / f'{A}.json'
    before = path.read_bytes()
    assert old['description_checked'] and old['resources_checked'] and old['attachment_inventory_count'] == 1
    calls = []
    def handler(request):
        calls.append(str(request.url))
        assert 'noticedesc' not in str(request.url)
        if refresh == 'failed':
            raise httpx.ConnectError('offline')
        return httpx.Response(200, json=body if refresh == 'recognized' else {'unknown': []})
    transport(monkeypatch, handler)
    got = nd.fetch_notice_depth(A)
    assert len(calls) == 1
    assert got['description_checked'] is False and got['description'] is None
    assert got['resources_checked'] is (refresh == 'recognized')
    assert got['attachment_inventory_count'] == (0 if refresh == 'recognized' else None)
    assert got['attachment_inventory_hash'] == (nd._manifest_hash([]) if refresh == 'recognized' else None)
    assert got['migration']['before']['attachment_inventory_count'] == 1
    assert got['migration']['requires_requirement_re_review'] and not got['migration']['approvals_copied']
    assert sam_capture.read_retained(tmp_path, got['legacy_depth_capture']) == before
    if refresh == 'recognized':
        assert len(got['withdrawn_attachments']) == 1
    assert any('untrusted' in x for x in got['errors'])
    persisted = json.loads(path.read_text())
    assert persisted['description_checked'] is False


@pytest.mark.parametrize('field,value', [('retrieved_at', []), ('description_checked', 'true'),
    ('resources_checked', 1), ('attachment_inventory_count', True), ('attachment_inventory_hash', []),
    ('withdrawn_attachments', [None]), ('resources_capture', []), ('description_capture', [])])
def test_typed_depth_corruption_requires_reconciliation(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    transport(monkeypatch, lambda request: httpx.Response(200, json=
        {'description': REQUIREMENT} if 'noticedesc' in str(request.url) else {'attachments': []}))
    nd.fetch_notice_depth(A, api_key='MOCK')
    path = tmp_path / f'{A}.json'
    cached = json.loads(path.read_text()); cached[field] = value
    cached['requirement_review'] = {'decision': 'approved'}
    path.write_text(json.dumps(cached))
    monkeypatch.delenv('SAM_GOV_API_KEY', raising=False)
    out = nd.fetch_notice_depth(A)
    assert out['resources_checked'] is True
    assert type(out['attachment_inventory_count']) is int
    assert isinstance(out['withdrawn_attachments'], list)
    assert 'requirement_review' not in out
    atomic_write_json(tmp_path / 'safe.json', out)


@pytest.mark.parametrize('link,expected', [
    (f'https://sam.gov/opp/{A.upper()}/view', 'confirmed_empty'),
    ('http://[broken', 'confirmed_empty'),
    (f'https://sam.gov/files/{FILE}?noticeid={B}', 'identity_mismatch'),
    (f'https://sam.gov/files/{FILE}#{B}', 'identity_mismatch'),
    (f'https://sam.gov/files/{FILE}#noticeId={B}', 'identity_mismatch'),
    (f'https://sam.gov/opportunities/resources/files/{B}/download', 'confirmed_empty')])
def test_link_identity_casefold_malformed_and_explicit_carriers(tmp_path, monkeypatch, link, expected):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    transport(monkeypatch, lambda request: httpx.Response(200, json={'noticeId': A.upper(),
        'attachments': [], '_links': {'self': {'href': link}}}))
    result = nd.fetch_notice_resources(A)
    assert result['collection_status'] == expected
    if link == 'http://[broken':
        assert any('uninspectable' in item for item in result['errors'])


def test_read_only_bytes_need_no_lock_write_and_still_check_hash(tmp_path):
    receipt = sam_capture.retain_bytes(tmp_path, b'read only')
    shutil.rmtree(tmp_path / 'object_locks')
    (tmp_path / 'objects').chmod(0o500); tmp_path.chmod(0o500)
    try:
        assert sam_capture.read_retained(tmp_path, receipt) == b'read only'
        assert not (tmp_path / 'object_locks').exists()
        with pytest.raises(ValueError):
            sam_capture.read_retained(tmp_path, {**receipt, 'bytes': 999})
    finally:
        tmp_path.chmod(0o700); (tmp_path / 'objects').chmod(0o700)


def test_retention_failed_refresh_uses_only_valid_stale_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv('LILA_NOTICE_CACHE_DIR', str(tmp_path))
    transport(monkeypatch, lambda request: httpx.Response(200, json={'attachments': []}))
    nd.fetch_notice_resources(A)
    path = tmp_path / f'{A}.resources.json'; cached = json.loads(path.read_text())
    cached['raw_capture']['received_at'] = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    path.write_text(json.dumps(cached))
    monkeypatch.setattr(sam_capture, 'retain_bytes', lambda *a: (_ for _ in ()).throw(OSError('read only')))
    out = nd.fetch_notice_resources(A)
    assert out['collection_status'] == 'stale_inventory'
    assert out['refresh_attempt']['collection_status'] == 'retention_failed'
    assert out['refresh_attempt']['diagnostic_inventory'] == []


def test_directory_conflict_and_exclusive_create_race_are_preserved(tmp_path, monkeypatch):
    payload = b'complete expected'; digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / 'objects' / f'{digest}.bin'
    path.mkdir(parents=True); (path / 'history').write_bytes(b'old directory')
    original_open = Path.open; raced = [False]
    monkeypatch.setattr(sam_capture.os, 'link', lambda *a: (_ for _ in ()).throw(OSError(errno.EPERM, 'no hardlink')))
    def opening(self, mode='r', *a, **kw):
        if self == path and mode == 'xb' and not raced[0]:
            raced[0] = True
            with original_open(self, 'wb') as f:
                f.write(b'uncooperative conflict')
        return original_open(self, mode, *a, **kw)
    monkeypatch.setattr(Path, 'open', opening)
    receipt = sam_capture.retain_bytes(tmp_path, payload)
    assert sam_capture.read_retained(tmp_path, receipt) == payload
    history = list((tmp_path / 'quarantine').iterdir())
    assert len(history) == 2
    assert any(x.is_dir() and (x / 'history').read_bytes() == b'old directory' for x in history)
    assert any(x.is_file() and x.read_bytes() == b'uncooperative conflict' for x in history)


def test_surrogate_and_bounded_diagnostics_survive_real_sweep_writer(tmp_path, monkeypatch):
    from tests.test_step7_followups import collect
    monkeypatch.setattr(sat, '_text_from_bytes', lambda *a, **kw: 'requirement\ud800text')
    _, receipt, _ = collect(tmp_path, monkeypatch, [public_file()])
    assert receipt['files'][0]['status'] == 'unreadable'
    atomic_write_json(tmp_path / 'sweep.json', {'results': {'attachment_record_receipts': {A: receipt}}})
    monkeypatch.setattr(sat, '_text_from_bytes', lambda *a, **kw: 'diagnostic ' * 1000)
    monkeypatch.setattr(sam_capture, 'retain_bytes', lambda *a: (_ for _ in ()).throw(OSError('read only')))
    result = sat.extract_public_attachment_text(public_file())
    assert result['collection_status'] == 'retention_failed'
    assert len(result['diagnostic_text']) == 1000 and result['diagnostic_text_truncated']
    assert result['text'] == ''
    atomic_write_json(tmp_path / 'diagnostic.json', result)
