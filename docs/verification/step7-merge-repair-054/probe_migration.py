"""Offline migration audit: saved inventories and read-only copied cache inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import types

ap = argparse.ArgumentParser()
ap.add_argument('--repo-root', type=Path, required=True)
ap.add_argument('--cache-snapshot', type=Path, required=True)
ap.add_argument('--output-dir', type=Path, required=True)
args = ap.parse_args()
root, snapshot, out = args.repo_root.resolve(), args.cache_snapshot.resolve(), args.output_dir.resolve()
out.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(root))
os.environ.pop('SAM_GOV_API_KEY', None)
attempts = []
def denied(*a, **kw):
    attempts.append('unexpected live socket')
    raise AssertionError('No live socket permitted in saved migration proof')
socket.socket.connect = denied
socket.getaddrinfo = denied
import httpx
from tools.api import _http, sam_notice_detail as nd, sam_attachment_text as sat
from run_dossiers import _source_depth_record

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

inputs = {str(p.relative_to(snapshot)): sha(p) for p in snapshot.rglob('*') if p.is_file()}
code = subprocess.check_output(['git', '-C', str(root), 'show',
    '67f644bc31bf19eadec628b738855e7b6c152a4e:tools/api/sam_notice_detail.py'], text=True)
base = types.ModuleType('base_depth_migration054'); base.__file__ = nd.__file__
exec(compile(code, 'base_depth_migration054', 'exec'), base.__dict__)
nd.sam_quota.note_call = lambda *a: None
proof = root / 'docs/verification/step7-052/SOURCE_PROOF.tar.gz'
observations = []
with tarfile.open(proof) as archive:
    for member in archive.getmembers():
        if not member.name.startswith('public-inventory-001/') or not member.name.endswith('.body'):
            continue
        notice = Path(member.name).stem
        raw = archive.extractfile(member).read()
        meta = json.load(archive.extractfile(member.name[:-5] + '.json'))
        assert hashlib.sha256(raw).hexdigest() == meta['raw_sha256']
        body = json.loads(raw)
        cache = out / 'base-written-depth' / notice; cache.mkdir(parents=True)
        os.environ['LILA_NOTICE_CACHE_DIR'] = str(cache)
        base.get_json = lambda url, **kw: {'description': 'Synthetic legacy description canary.'} if 'noticedesc' in url else body
        old = base.fetch_notice_depth(notice, api_key='SYNTHETIC_ONLY')
        def handler(request):
            assert str(request.url) == nd.RESOURCES_URL_TPL.format(id=notice)
            return httpx.Response(200, content=raw)
        _http._client = lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        current = nd.fetch_notice_depth(notice)
        active, recognized = nd._attachment_inventory(body, notice)
        history = nd._withdrawn_inventory(body, notice)
        assert current['description_checked'] is False and current['description'] is None
        assert current['resources_checked'] is recognized
        assert current['attachment_inventory_count'] == (len(active) if recognized else None)
        assert current['attachment_inventory_hash'] == (nd._manifest_hash(active) if recognized else None)
        assert current['withdrawn_attachments'] == (history if recognized else [])
        projected = _source_depth_record(notice, f'https://sam.gov/opp/{notice}/view', current)
        assert projected['withdrawn_attachments'] == current['withdrawn_attachments']
        assert projected['migration'] == current['migration']
        observations.append({'notice_id': notice, 'recognized': recognized,
            'original_response_sha256': hashlib.sha256(raw).hexdigest(),
            'before': nd._depth_inventory_snapshot(old), 'after': nd._depth_inventory_snapshot(current),
            'hash_changed': old.get('attachment_inventory_hash') != current.get('attachment_inventory_hash'),
            'withdrawn_count': len(history), 'requires_requirement_re_review': True,
            'approval_copied': False, 'description_authority_withheld': True})
assert len(observations) == 21
recognized = [r for r in observations if r['recognized']]
assert len(recognized) == 17 and all(r['hash_changed'] for r in recognized)
assert sum(r['withdrawn_count'] == 0 for r in recognized) == 13

# All old cache files copied by root are inspected on a second shadow copy.
# No raw response or metadata is invented to make an old entry validate.
shadow = out / 'operating-cache-shadow'; shutil.copytree(snapshot, shadow)
os.environ['LILA_NOTICE_CACHE_DIR'] = str(shadow / 'data/cache/sam_notice')
os.environ['LILA_SAM_ATTACHMENT_TEXT_DIR'] = str(shadow / 'data/cache/sam_attachment_text')
refused_attempts = []
def unavailable(*a, **kw):
    refused_attempts.append('source unavailable in offline migration audit')
    raise OSError('offline migration audit: original source acquisition unavailable')
nd.get_json = unavailable
sat._download_bytes = unavailable
cache_rows = []
for rel in sorted(inputs):
    p = snapshot / rel
    if p.parent.name not in ('sam_notice', 'sam_attachment_text') or p.suffix != '.json':
        continue
    old = json.loads(p.read_text())
    if p.parent.name == 'sam_notice':
        notice = p.name.split('.')[0]
        current = nd.fetch_notice_resources(notice) if '.resources.' in p.name else nd.fetch_notice_depth(notice)
        assert not current.get('resources_checked')
        assert current.get('attachment_inventory_count') is None and current.get('attachment_inventory_hash') is None
        cache_rows.append({'path': rel, 'kind': 'inventory' if '.resources.' in p.name else 'depth',
            'original_sha256': inputs[rel], 'before': nd._depth_inventory_snapshot(old),
            'after': nd._depth_inventory_snapshot(current), 'needs_source_reacquisition': True,
            'requires_requirement_re_review': True, 'approval_copied': False})
    else:
        resource = p.stem
        file = {'resource_id': resource, 'name': old.get('name') or 'legacy.txt', 'access_level': 'public'}
        current = sat.extract_public_attachment_text(file)
        assert not current.get('text') and current['collection_status'] != 'captured_discovery_only'
        cache_rows.append({'path': rel, 'kind': 'text', 'original_sha256': inputs[rel],
            'before_text_characters': len(old.get('text') or ''), 'before_source_hash': old.get('sha256'),
            'after_text_characters': 0, 'after_source_hash': current.get('sha256'),
            'needs_source_reacquisition': True, 'requires_requirement_re_review': True,
            'approval_copied': False})
assert all(sha(snapshot / rel) == digest for rel, digest in inputs.items())
assert not attempts
result = {'status': 'PASS', 'scope': 'Saved-byte inventory migration plus offline shadow audit of root-copied caches; no operating migration.',
    'exact_checkout_sha': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
    'source_archive_sha256': sha(proof), 'saved_inventory_responses': len(observations),
    'recognized': len(recognized), 'recognized_hash_changes': sum(r['hash_changed'] for r in recognized),
    'changed_without_withdrawn_rows': 13, 'saved_depth_migrations': observations,
    'old_cache_files': len(cache_rows), 'old_cache_dispositions': cache_rows,
    'old_operating_depth_count': sum(r['kind'] == 'depth' for r in cache_rows),
    'root_snapshot_unchanged': True, 'root_snapshot_sha256': inputs,
    'live_socket_attempts': len(attempts), 'mocked_unavailable_attempts': len(refused_attempts),
    'clock_limit': 'Synthetic audit acquisition time; not replay-time freshness or source authentication.',
    'description_limit': 'Base-written saved-inventory controls use a synthetic legacy description; all remain untrusted.',
    'production_merge': False}
(out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if k not in ('saved_depth_migrations', 'old_cache_dispositions', 'root_snapshot_sha256')}))
