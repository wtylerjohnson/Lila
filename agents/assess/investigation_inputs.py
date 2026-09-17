"""Read existing stores once during a sweep, freeze exact joins for research.

No enrichment, migrations, title-based event joins, or press-time I/O.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from agents.assess.contracts import EvidenceRef
from agents.assess.research_subjects import canonical, digest


def _official(url):
    parts = urlsplit(str(url or ''))
    return parts.scheme == 'https' and (parts.hostname or '').endswith(('.gov', '.mil'))


def _reference(record, *, source, url, kind='notice', primary=True):
    sha = digest(record)
    return EvidenceRef(evidence_id='ev:investigation:' + sha, tier='notice' if primary else 'discovery',
        kind=kind, source_name=source, source_url=url, record_hash=sha,
        excerpt=canonical(record), primary_source=primary,
        supports=('buyer',) if primary else ()).model_dump(mode='json')


def _contract_ids(subject):
    row = json.loads(subject.source_payload_json)
    values = [row.get('predecessor_contract_id'), row.get('piid'), row.get('award_id'),
              (row.get('source_fields') or {}).get('contract_number')]
    # Explicit structured contract fields only. Never mine a program title.
    return sorted({token for value in values if value for token in re.findall(r'\b[A-Z0-9]{10,24}\b', str(value).upper())})


def input_binding(subjects, profile, cutoff):
    return digest({'source_cutoff': cutoff.isoformat(), 'profile': profile.model_dump(mode='json'),
                   'subjects': {s.subject_id: s.source_sha256 for s in subjects}})


def published_routing_contacts(evidence, *, organization):
    """Parse the explicit Federal Register routing block, not inferred owners."""
    contacts = {}
    pattern = re.compile(r'FOR\s*FURTHER\s*INFORMATION\s*CONTACT:\s*'
        r'(?P<name>[A-Z][A-Za-z .’\'-]{3,80}),\s*(?P<role>[^,]{3,100}),'
        r'.{0,300}?by phone at\s*(?P<phone>[+\d() –-]{7,30})\s*or email at\s*'
        r'(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?i:gov|mil))\b')
    for ref in evidence:
        if not ref.primary_source or not _official(str(ref.source_url)):
            continue
        text = re.sub(r'\s+', ' ', ref.excerpt)
        for match in pattern.finditer(text):
            values = {k:v.strip() for k,v in match.groupdict().items()}
            if re.search(r'Human Resources|recruit(?:ing|ment)|employment', match.group(), re.I):
                continue
            item = dict(**values, organization=organization,
                source_kind='government_published', source_url=str(ref.source_url),
                contact_status='Published rule routing contact; office phone, not established as mobile',
                authority_boundary='Published office routing contact; technical ownership and purchasing authority unconfirmed',
                evidence_ids=[ref.evidence_id])
            contacts[digest(item)] = item
    return contacts


def capture(subjects, profile, cutoff):
    from tools.notice_store import db_path, last_ingest
    from tools.contact_graph.store import ContactGraphStore
    from agents.golden_press import targets_store
    from tools.slug import client_slug
    result = {'schema_version': 'investigation-inputs.v1', 'input_binding': input_binding(subjects, profile, cutoff),
              'captured_at': datetime.now(timezone.utc).isoformat(),
              'subjects': {}, 'store_receipts': {}}
    try:
        sightings = ContactGraphStore().read_observations()
        result['store_receipts']['contacts'] = {'state': 'read', 'count': len(sightings)}
    except (OSError, ValueError) as exc:
        sightings = []
        result['store_receipts']['contacts'] = {'state': 'failed', 'error': str(exc)}
    path = targets_store.store_path(client_slug(profile.client_name))
    try:
        raw_targets = json.loads(path.read_text()) if path.exists() else None
        targets, dropped = targets_store.admissible(raw_targets)
        # Admissibility sanitizes identities, while the original retains catchall
        # and conflicting-role flags. Both must be preserved in this snapshot.
        original_targets = {(r.get('contact_id'), r.get('spec_id')): r for r in (raw_targets or {}).get('contacts', [])}
        metadata = ('email_domain_catchall', 'email_domain_catchall_verdict', 'email_locked', 'role_conflict', 'excluded_reason')
        targets = [{**{k: v for k, v in original_targets.get((r.get('contact_id'), r.get('spec_id')), {}).items() if k in metadata}, **r} for r in targets]
        result['store_receipts']['saved_targets'] = {'state': 'read' if raw_targets is not None else 'not_available',
                                                   'count': len(targets), 'dropped': dropped}
    except (OSError, ValueError, TypeError) as exc:
        targets = []
        result['store_receipts']['saved_targets'] = {'state': 'failed', 'error': str(exc)}
    conn = None
    try:
        source_path = Path(os.environ.get('LILA_INVESTIGATION_NOTICE_STORE_PATH', db_path()))
        if source_path.exists():
            conn = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row
            result['store_receipts']['notices'] = {'state': 'read', 'last_ingest': last_ingest(conn=conn)}
        else:
            result['store_receipts']['notices'] = {'state': 'not_available'}
        for subject in subjects:
            refs, contacts, joined = {}, {}, []
            ids = _contract_ids(subject)
            state = 'not_run_no_contract_identity'
            if ids and conn:
                try:
                    clauses = ['(UPPER(award_number)=? OR UPPER(description_prefix) LIKE ?)'] * len(ids)
                    args = [value for token in ids for value in (token, '%' + token + '%')]
                    rows = conn.execute('SELECT * FROM notices WHERE ' + ' OR '.join(clauses) + ' LIMIT 51', args).fetchall()
                    state = 'historical_store_nonmatch' if not rows else 'matched'
                    for entry in rows[:50]:
                        notice = dict(entry)
                        available = str(notice.get('last_seen') or '')[:10]
                        if not available or available > cutoff.date().isoformat() or not _official(notice.get('url')):
                            continue
                        text = str(notice.get('award_number') or '') + '\n' + str(notice.get('description_prefix') or '')
                        matched = [token for token in ids if re.search(r'(?<![A-Z0-9])' + re.escape(token) + r'(?![A-Z0-9])', text.upper())]
                        if not matched:
                            continue
                        ref = _reference(notice, source='sam.gov historical notice store', url=notice['url'])
                        refs[ref['evidence_id']] = ref
                        joined.append({'notice_id': notice['notice_id'], 'matched_contract_ids': matched,
                                       'evidence_id': ref['evidence_id'], 'historical_only': True})
                        if notice.get('poc_name'):
                            item = dict(name=notice['poc_name'], role=notice.get('poc_title') or 'Published contracting POC',
                                organization=notice.get('office') or notice.get('agency') or subject.agency,
                                source_kind='government_published', source_url=notice['url'],
                                email=notice.get('poc_email'), phone=notice.get('poc_phone'),
                                contact_status='Historical notice POC; current role unconfirmed',
                                authority_boundary='Contracting routing contact; not established as technical owner or current buyer',
                                evidence_ids=[ref['evidence_id']])
                            contacts[digest(item)] = item
                    if rows and not joined:
                        state = 'historical_store_no_admissible_match'
                    if len(rows) > 50:
                        state = 'partial_capped'
                except sqlite3.Error as exc:
                    state = 'failed: ' + str(exc)
            elif ids:
                state = 'store_unavailable'
            for obs in sightings:
                if (obs.source != subject.source_system or obs.notice_id != subject.source_record_id
                        or not obs.person_name or obs.person_name == 'UNATTRIBUTED'
                        or not _official(obs.source_url) or obs.harvested_at.tzinfo is None or obs.harvested_at > cutoff):
                    continue
                ref = _reference(obs.model_dump(mode='json'), source=obs.source, url=obs.source_url)
                refs[ref['evidence_id']] = ref
                item = dict(name=obs.person_name, role=obs.title or obs.role_type or 'Published routing POC',
                    organization=obs.office_path or obs.agency or subject.agency,
                    source_kind='government_published', source_url=obs.source_url,
                    email=obs.channel_value if obs.channel_kind == 'email' else None,
                    phone=obs.channel_value if obs.channel_kind == 'phone' else None,
                    contact_status='Saved source sighting; current program role unconfirmed',
                    authority_boundary='Contact channel does not establish current technical or purchasing authority',
                    evidence_ids=[ref['evidence_id']])
                contacts[digest(item)] = item
            for saved in targets:
                if not {subject.subject_id, subject.source_record_id}.intersection(saved.get('join_record_ids') or []):
                    continue
                if saved.get('provenance', {}).get('class') != 'apollo':
                    continue
                date = str(saved.get('provenance', {}).get('retrieved_at') or '')[:10]
                if not date or date > cutoff.date().isoformat():
                    continue
                # Conflicting office holders are kept for adjudication, never
                # selected by CRM modification time or synthetic freshness.
                if saved.get('role_conflict') or saved.get('excluded_reason'):
                    continue
                url = saved.get('linkedin_url') or 'https://app.apollo.io/#/contacts/' + str(saved.get('contact_id') or '')
                ref = _reference(saved, source='Apollo saved contact', url=url, kind='web_lead', primary=False)
                refs[ref['evidence_id']] = ref
                status = 'Provider email status: ' + str(saved.get('email_status') or 'unknown')
                status += '; domain catch-all: ' + str(saved.get('email_domain_catchall', 'unknown'))
                item = dict(name=saved['name'], role=saved.get('title') or 'Unconfirmed routing candidate',
                    organization=saved.get('organization') or subject.agency,
                    source_kind='apollo', source_url=url,
                    email=saved.get('email') if not saved.get('email_locked') else None,
                    phone=None,  # employer switchboards are not personal direct dials
                    contact_status=status + '; current program role unconfirmed',
                    authority_boundary='Saved enrichment only; employment, program responsibility and buying authority unconfirmed',
                    evidence_ids=[ref['evidence_id']])
                contacts[digest(item)] = item
            result['subjects'][subject.subject_id] = {'evidence': list(refs.values()), 'contacts': contacts,
                'contract_join': {'state': state, 'contract_ids': ids, 'records': joined}}
    finally:
        if conn:
            conn.close()
    return result
