"""Retain already-collected follow-up sources and contacts before normal synthesis.

This research input is not a reviewed-case overlay or a permission receipt.
The source identity join is explicit; no title/agency similarity joins.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import os

from agents.assess.contracts import EvidenceRef
from agents.leadgen.targets import LeadTarget
from tools.slug import client_slug


def input_path(client):
    return Path(os.environ.get('LILA_INVESTIGATION_SUPPLEMENTS_PATH',
        Path(__file__).resolve().parents[2] / 'data/review' / (client_slug(client) + '.research_inputs.json')))


def fingerprint(client):
    path = input_path(client)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read_inputs(client, subjects, cutoff):
    path = input_path(client)
    if not path.is_file():
        return {}, {'state': 'not_available'}
    blob = path.read_bytes()
    book = json.loads(blob)
    if book.get('version') != 'research-inputs.v1' or book.get('client_name') != client:
        raise ValueError('research follow-up inputs have the wrong client or schema')
    index = {(s.source_system, s.source_record_id): s for s in subjects}
    output = {}
    for row in book['records']:
        subject = index.get((row['source_system'], row['source_record_id']))
        if subject is None:
            continue
        captured = datetime.fromisoformat(row['captured_at'])
        if captured.tzinfo is None or captured > cutoff:
            raise ValueError('research input capture is undated or later than the sweep cutoff')
        refs = {e.evidence_id: e for raw in row['evidence'] for e in [EvidenceRef.model_validate(raw)]}
        contacts = {}
        from .research_subjects import digest
        for raw in row.get('contacts', []):
            target = LeadTarget.model_validate(raw)
            if any(refs.get(e.evidence_id) != e for e in target.evidence):
                raise ValueError('research input contact differs from its retained evidence')
            item = target.model_dump(mode='json')
            item['evidence_ids'] = [e.evidence_id for e in target.evidence]
            for field in ('evidence', 'route', 'reason_to_contact', 'next_ask'):
                item.pop(field)
            contacts[digest(item)] = item
        prior = output.setdefault(subject.subject_id, {'evidence': {}, 'contacts': {}})
        for key, ref in refs.items():
            if key in prior['evidence'] and prior['evidence'][key] != ref.model_dump(mode='json'):
                raise ValueError('conflicting research source evidence IDs')
            prior['evidence'][key] = ref.model_dump(mode='json')
        prior['contacts'].update(contacts)
    return output, {'state': 'read', 'sha256': hashlib.sha256(blob).hexdigest(),
                    'subject_count': len(output), 'path': str(path)}
