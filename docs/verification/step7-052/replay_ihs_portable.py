"""Replay retained public SAM bytes through native enrichment, without a network.

Run with --repo-root pointing to the exact review checkout and --source-root to
the unpacked source-proof directory. A new --output-dir is required each time.
This driver freezes file acquisition clocks at their original observed values;
it does not upgrade saved bytes into a new acquisition or requirement approval.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import patch


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo-root', required=True, type=Path)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    root, source, output = args.repo_root.resolve(), args.source_root.resolve(), args.output_dir.resolve()
    manifest = json.loads((source / 'SOURCE_MANIFEST.json').read_text())
    for item in manifest['files']:
        body = (source / item['path']).read_bytes()
        assert sha(body) == item['sha256'] and len(body) == item['bytes'], item['path']
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root))
    import run_searches as searches
    from tools.api import sam_attachment_text as sat, sam_notice_detail as detail
    from tools.api.sam_extract import _to_opportunity
    from tools.relevance.taxonomy import CapabilityTaxonomy

    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    family = json.loads((source / 'local-source-002/IHS_ROWS.json').read_text())
    row = next(r['normalized'] for r in family['rows']
               if r['normalized']['notice_id'] == '0caab6e53d8b47d7850692b7684610bf')
    candidate = _to_opportunity(row)
    inv = json.loads((source / 'public-inventory-001' / (candidate.source_id + '.json')).read_text())
    raw_inventory = (source / 'public-inventory-001' / (candidate.source_id + '.body')).read_bytes()
    assert sha(raw_inventory) == inv['raw_sha256']
    attachments, recognized = detail._attachment_inventory(json.loads(raw_inventory), candidate.source_id)
    assert recognized and attachments == inv['attachments']
    inventory = {'id': candidate.source_id, 'attachments': attachments,
                 'attachment_inventory_hash': detail._manifest_hash(attachments),
                 'resources_checked': True, 'retrieved_at': inv['completed_at'],
                 'collection_status': 'inventory_captured', 'authority': 'discovery_only',
                 'raw_capture': {'path': str(source / 'public-inventory-001' / (candidate.source_id + '.body')),
                                 'sha256': sha(raw_inventory), 'bytes': len(raw_inventory)}}
    tax_path = source / 'taxonomy/capability_taxonomy.json'
    taxonomy = CapabilityTaxonomy.model_validate_json(tax_path.read_text())
    files = {r['attachment']['resource_id']: r for r in json.loads((source / 'ihs-files-001/RESULTS.json').read_text())}
    replay_clock = {'now': datetime.fromisoformat(inv['completed_at'])}
    loads = []

    class ReplayClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return replay_clock['now'].astimezone(tz) if tz else replay_clock['now'].replace(tzinfo=None)

    def saved_bytes(rid, **kwargs):
        if rid not in files:
            raise FileNotFoundError('file not fetched in original bounded collection; no replay acquisition allowed')
        receipt = files[rid]
        data = (source / 'ihs-files-001' / (rid + '.pdf')).read_bytes()
        assert sha(data) == receipt['raw_sha256']
        replay_clock['now'] = datetime.fromisoformat(receipt['retrieved_at'])
        loads.append(rid)
        return data

    class Source:
        last_attachment_census = {'selected': 1, 'scope': 'explicit saved IHS selected family; not new search'}

        def attachment_candidates(self, *args, **kwargs):
            return [candidate]

    def deny_network(*args, **kwargs):
        raise AssertionError('saved-source replay cannot access a network')

    started = datetime.now(timezone.utc).isoformat()
    with patch.dict(os.environ, {'LILA_SAM_ATTACHMENT_TEXT_DIR': str(output / 'text-cache')}), \
            patch.object(sat, 'datetime', ReplayClock), patch.object(sat, '_download_bytes', saved_bytes), \
            patch.object(detail, 'fetch_notice_resources', lambda *args, **kwargs: inventory), \
            patch('tools.api.sam_quota.calls_today', return_value=0), \
            patch.object(socket.socket, 'connect', deny_network), patch.object(socket, 'create_connection', deny_network):
        rows, stats = searches._enrich_sam_public_attachments(
            [json.loads(candidate.model_dump_json())], Source(), object(), taxonomy, None, limit=1)
    assert len(rows) == 1 and stats['attachment_relevant'] == stats['attachment_added'] == stats['attachment_sam_calls'] == 0
    assert set(loads) == set(files)
    for name, content in [('ROWS.json', rows), ('RECEIPT.json', stats)]:
        (output / name).write_text(json.dumps(content, indent=2) + '\n')
    modules = ['run_searches.py', 'tools/api/sam_attachment_text.py', 'tools/api/sam_capture.py',
               'tools/api/sam_notice_detail.py', 'tools/api/sam_extract.py']
    result = {'schema': 'lila.step7.saved_source_replay.v1', 'exact_checkout_sha': revision,
              'driver_sha256': sha(Path(__file__).read_bytes()),
              'module_sha256': {name: sha((root / name).read_bytes()) for name in modules},
              'source_manifest_sha256': sha((source / 'SOURCE_MANIFEST.json').read_bytes()),
              'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
              'source_notice_id': candidate.source_id, 'loaded_files': loads,
              'taxonomy_sha256': sha(tax_path.read_bytes()),
              'clock_policy': 'Original per-file observed acquisition times; no replay-time freshness',
              'candidate_rows_retained': len(rows), 'attachment_relevant': stats['attachment_relevant'],
              'attachment_added': stats['attachment_added'], 'metered_calls': stats['attachment_sam_calls'],
              'strict_assess_approval': False, 'seller_ready_claim': False,
              'scope': 'Hash-verified saved-original-byte native-function replay with network blocked; not fresh acquisition or full UI release'}
    (output / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
