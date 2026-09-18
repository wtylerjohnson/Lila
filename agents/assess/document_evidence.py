"""Supporting evidence from the ordinary verified document-capture adapter.

The original ResearchSubject and its untrusted research_record clock are intact.
A capture receipt proves acquisition, never current demand or buying authority.
"""
import hashlib
from urllib.parse import urlsplit

from .contracts import EvidenceRef
from .research_subjects import digest
from .source_clock import acquisition_clock, clock_instant


def captured_evidence(row, cutoff):
    packet = row.get('document_evidence') or {}
    capture = packet.get('document') or {}
    text = row.get('description') or ''
    if row.get('source') != 'agency_program_documents' or not packet:
        return None
    if (capture.get('status') != 'captured' or capture.get('http_status') != 200
            or capture.get('method') != 'GET' or not capture.get('finished_at')
            or not capture.get('raw_sha256') or len(capture['raw_sha256']) != 64
            or capture.get('final_url') != row.get('canonical_url')
            or hashlib.sha256(text.encode()).hexdigest() != packet.get('extraction_sha256')
            or not any(a.get('state') == 'success' and a.get('tls_verified') is True
                       for a in capture.get('attempts', []))):
        return None
    host = urlsplit(capture['final_url']).hostname or ''
    if not capture['final_url'].startswith('https://') or not host.endswith(('.gov', '.mil')):
        return None
    binding = digest({'source_record': row['record_id'], 'document': capture,
                      'extraction_sha256': packet['extraction_sha256']})
    clock = acquisition_clock(capture['finished_at'], basis='program_document_capture',
        component='program_document', field='document_evidence.document.finished_at', binding=binding)
    stamp = clock_instant(clock)
    if stamp is None or stamp > cutoff:
        return None
    return EvidenceRef(evidence_id='ev:captured-program:' + binding, tier='program',
        kind=row['kind'], source_name='Verified publisher document capture',
        source_url=row['canonical_url'], retrieved_at=stamp, source_acquisition=clock,
        record_hash=binding, excerpt=text, primary_source=True,
        supports=('requirement', 'timing', 'buyer', 'access'))
