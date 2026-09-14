"""Bounded synthetic probe of real 3-file and dossier character ceilings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--repo-root', type=Path, required=True)
parser.add_argument('--output-dir', type=Path, required=True)
args = parser.parse_args()
root, out = args.repo_root.resolve(), args.output_dir.resolve()
out.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(root))
import run_searches as searches
from agents.decisions.dossier import _attachment_research_context
from agents.schemas import RawOpportunity
from tools.api import sam_attachment_text as sat, sam_notice_detail as detail
from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm

files = [{'resource_id': letter * 32, 'name': f'SOW {i}.txt', 'access_level': 'public',
          'access_status': 'public', 'export_controlled': '0', 'explicit_access': '0'}
         for i, letter in enumerate('bcd')]
prefix = 'network visibility Café Δ '
content = {item['resource_id']: (prefix + 'é' * 50000)[:50000] for item in files}
inventory = detail._manifest_hash(files)
candidate = RawOpportunity(source='sam.gov', source_id='a' * 32, title='Synthetic boundary',
                           agency='NASA', raw_payload={'type': 'Solicitation'})

class Source:
    last_attachment_census = {'selected': 1, 'scope': 'synthetic ceiling probe'}
    def attachment_candidates(self, *args, **kwargs):
        return [candidate]

with patch.dict(os.environ, {'LILA_SAM_ATTACHMENT_TEXT_DIR': str(out / 'cache')}), \
        patch('tools.api.sam_quota.calls_today', return_value=0), \
        patch.object(detail, 'fetch_notice_resources', return_value={
            'id': candidate.source_id, 'resources_checked': True, 'attachments': files,
            'attachment_inventory_hash': inventory}), \
        patch.object(sat, '_download_bytes', lambda rid, **kwargs: content[rid].encode()):
    rows, stats = searches._enrich_sam_public_attachments(
        [], Source(), object(), CapabilityTaxonomy(client_name='Testco', version=1,
            updated='2026-07-21', core=[TaxonomyTerm(term='network visibility')]), None, limit=1)
assert len(rows) == 1
raw = rows[0]['raw_payload']
context = _attachment_research_context(rows[0], {'id': candidate.source_id,
                                                'attachment_inventory_hash': inventory})
assert len(raw['text']) == 150000
assert context['status'] == 'discovery_only' and len(context['text']) == 14000
assert sum(len(item['text']) for item in context['file_passages']) == 14000
locators = [item['combined_text_locator'] for item in raw['attachment_evidence']]
assert [(item['start'], item['end']) for item in locators] == [(0, 50000), (50002, 100002), (100004, 150000)]
assert [item['file_text_truncated'] for item in raw['attachment_evidence']] == [False, False, True]
for item in raw['attachment_evidence']:
    loc = item['combined_text_locator']
    assert hashlib.sha256(raw['text'][loc['start']:loc['end']].encode()).hexdigest() == item['text_sha256']
assert len(raw['text'].encode()) > len(raw['text'])
result = {'scope': 'Synthetic original native collector/dossier ceiling probe, no source acquisition',
          'exact_checkout_sha': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
          'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'status': 'PASS',
          'combined_characters': len(raw['text']), 'dossier_characters': len(context['text']),
          'file_locators': locators, 'third_file_truncated_characters': 4,
          'metered_calls': stats['attachment_sam_calls'], 'authority': context['status']}
(out / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
