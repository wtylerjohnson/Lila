"""Read-only notice admission from current original inputs and bound Assess review.

Serialized packs carry diagnostics, never admission authority. This reader opens
only the selected root's ordinary inputs, revalidates the immutable run, and
rebuilds its live projection without materializing or approving anything.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from agents.assess import ledger
from agents.assess.approval import _scope_and_sweep, current_assess_binding
from agents.assess.binding import binding_drift
from agents.assess.contracts import AssessRun
from agents.assess.source_clock import acquired_at
from tools.capability import ClientProfile
from tools.relevance.notice_family import digest, resolve
from tools.relevance.requirement_support import requirement_support
from tools.relevance.response_action import response_instruction
from tools.relevance.taxonomy import CapabilityTaxonomy
from tools.relevance.temporal import instant, record_time, temporal_assessment

VERSION = 'native-notice-admission-v1'


def source_transport(row, *, ordinal=None, locator=''):
    return dict(version='notice-source-v1', original_record=deepcopy(row),
                record_sha256=digest(row), native_ordinal=ordinal, locator=locator)


def access_decision(row, certifications):
    """Only explicit recognized source access can establish a direct route."""
    raw = row.get('raw_payload') or {}
    raw = raw if isinstance(raw, dict) else {}
    aliases = {
        'unrestricted': 'open', 'full and open': 'open',
        'full and open competition': 'open', 'none': 'open',
        'no set aside used': 'open', 'no set-aside used': 'open',
        'sba': 'small_business', 'total_small_business': 'small_business',
        'total small business set-aside': 'small_business',
        'total small business set-aside (far 19.5)': 'small_business',
        '8a': '8a', '8(a)': '8a', '8(a) set-aside': '8a',
        '8(a) set-aside (far 19.8)': '8a',
        'sdvosbc': 'sdvosb', 'sdvosbs': 'sdvosb', 'sdvosb': 'sdvosb',
        'iee': 'iee', 'isbee': 'isbee',
        'hubzone': 'hubzone', 'hz': 'hubzone', 'wosb': 'wosb', 'edwosb': 'edwosb',
    }
    claims = [str(d[k]).strip().casefold() for d in (raw, row)
              for k in ('set_aside', 'set_aside_code', 'typeOfSetAside', 'typeOfSetAsideDescription')
              if d.get(k) is not None and str(d[k]).strip()]
    parsed = {aliases.get(c, 'unknown') for c in claims}
    cert_aliases = {'small business': 'small_business', 'small_business': 'small_business',
                    'sba': 'small_business', '8(a)': '8a', '8a': '8a',
                    'indian economic enterprise': 'iee', 'indian small business economic enterprise': 'isbee',
                    'iee': 'iee', 'isbee': 'isbee', 'sdvosb': 'sdvosb', 'hubzone': 'hubzone', 'wosb': 'wosb', 'edwosb': 'edwosb'}
    certs = {cert_aliases.get(str(c).strip().casefold()) for c in certifications}
    access = next(iter(parsed)) if len(parsed) == 1 else 'unknown'
    eligible = access == 'open' or (access != 'unknown' and access in certs)
    return dict(route_relationship='direct' if eligible else 'unknown',
                commercial_route='direct' if eligible else 'restricted_route_needed',
                eligible_route=eligible, original_access_claims=claims,
                route_basis=('explicit source access and approved client eligibility' if eligible else
                             'source access is missing, conflicting, unrecognized or lacks approved eligibility'))


class NoticeReadContext:
    """An in-process reader; saved dictionary receipts cannot substitute for it."""

    def __init__(self, root, client_name, slug, as_of):
        self.root, self.client_name, self.slug = Path(root).resolve(), client_name, slug
        self.as_of = instant(as_of)
        self.gaps, self.files, self.sources, self.decisions = [], {}, {}, {}
        self.families, self.parents, self.sweep, self.taxonomy = [], {}, {}, None
        self.run_id = None
        try:
            self._load()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.gaps.append(f'Current notice admission unavailable: {type(exc).__name__}: {exc}')
        self.fingerprint = digest(dict(version=VERSION, files=self.files,
            as_of=str(self.as_of), gaps=self.gaps, families=self.families))

    def _read(self, path, *, optional=False):
        path = Path(path)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            if not optional:
                raise
            self.files[str(path)] = 'missing'
            return None
        import hashlib
        self.files[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    def unchanged(self):
        import hashlib
        for name, expected in self.files.items():
            try:
                actual = hashlib.sha256(Path(name).read_bytes()).hexdigest()
            except FileNotFoundError:
                actual = 'missing'
            except OSError:
                return False
            if actual != expected:
                return False
        return True

    def _load(self):
        if self.as_of is None:
            raise ValueError('current graph requires an explicit aware presentation clock')
        review = self.root / 'data' / 'review'
        self._read(review / f'{self.slug}.review.json', optional=True)
        designator, path = _scope_and_sweep(self.client_name, str(review))
        self.sweep = self._read(path)
        self.profile_json = self._read(self.root / 'clients' / self.slug / 'profile.json')
        profile = ClientProfile.model_validate(self.profile_json)
        self.taxonomy = CapabilityTaxonomy.model_validate(self._read(
            self.root / 'clients' / self.slug / 'capability_taxonomy.json'))
        if self.taxonomy.client_name != self.client_name or profile.client_name != self.client_name:
            raise ValueError('profile or taxonomy belongs to another client')
        binding = current_assess_binding(self.client_name, review_dir=str(review),
            sweep_path=str(path), sweep=self.sweep, profile=self.profile_json)
        rows = (self.sweep.get('results') or {}).get('sam.gov') or []
        rows = [r for r in rows if isinstance(r, dict)]
        self.families = resolve(rows)
        for family in self.families:
            for member in family['members']:
                sid = member['source_id']
                if sid in self.sources:
                    raise ValueError(f'duplicate or conflicting source snapshots for {sid}')
                self.sources[sid] = member['original_record']
        state = self.root / 'data' / 'state' / 'assess_runs'
        pointer = ledger.current_assess_pointer_path(self.client_name, designator, state_dir=state)
        pointer_data = self._read(pointer)
        payload = ledger.load_current_assess_run(self.client_name, designator, state_dir=state)
        if payload is None or payload.get('schema_version') == 1:
            raise ValueError('missing, invalid or legacy-only current Assess run')
        self._read(pointer.parent / pointer_data['artifact'])
        drift = binding_drift(payload.get('binding'), binding,
                              subject='graph source', remedy='refresh the strict run')
        if drift:
            raise ValueError('; '.join(drift))
        manifest = ledger.assess_projection_input_manifest(self.client_name, review_dir=review)
        if payload.get('projection_inputs') != manifest:
            raise ValueError('current Assess review or reference inputs changed')
        # Track the local mutable review inputs as well as canonical comparison.
        for suffix in ('horizon', 'reviewed_cases', 'qualify', 'assess_approval', 'partnering'):
            self._read(review / f'{self.slug}.{suffix}.json', optional=True)
        reviews = self._read(review / f'{self.slug}.live_requirements.json', optional=True)
        run = AssessRun.model_validate(payload['run'])
        self.run_id = run.run_id
        live, index, _, diagnostics = ledger.adapt_live_ledger(self.sweep, profile,
            run_id=run.run_id, scope=run.scope, profile_version=run.profile_version,
            as_of=self.as_of, requirement_reviews_payload=reviews, binding=binding)
        stored = {r.record_id: r for r in run.live.records}
        for parent in live.records:
            prior = stored.get(parent.record_id)
            if (prior and prior.notice_id == parent.notice_id and
                prior.requirement_review_evidence_id == parent.requirement_review_evidence_id and
                prior.classification.value == 'bid_now' and prior.recommendation.value == 'pursue'):
                self.parents[parent.notice_id] = parent
        # The live adapter's own diagnostics are retained separately from fatal rebind gaps.
        self.projection_diagnostics = diagnostics
        if not self.unchanged():
            raise ValueError('source or pointer changed during graph read')

    def family_for(self, sid):
        return next((f for f in self.families if sid in f['member_ids']), None)

    def decision(self, record, *, recheck=True):
        sid = str(record.get('record_id') or '')
        gaps = list(self.gaps)
        original = self.sources.get(sid)
        family = self.family_for(sid)
        parent = self.parents.get(sid)
        support, timing, action, route = {}, {}, {}, access_decision({}, [])
        if recheck and not self.unchanged():
            gaps.append('Source or pointer changed after graph snapshot')
        if original is None:
            gaps.append('No exact original notice in selected source census')
        else:
            transport = (record.get('source_fields') or {}).get('notice_source_v1')
            if transport and (transport.get('original_record') != original or
                              transport.get('record_sha256') != digest(original)):
                gaps.append('Saved source transport disagrees with original source')
            raw = original.get('raw_payload') or {}
            depth = ledger._dossier_depth(self.sweep.get('results') or {}).get(sid)
            if not ledger._depth_matches_posting(depth, sid):
                gaps.append('Native same-notice description depth is unavailable')
                depth = {}
            description = (depth or {}).get('description') or ''
            active = [str(d[k]).strip().casefold() for d in (original, raw) for k in ('active',)
                      if d.get(k) is not None]
            types = [str(d[k]).strip().casefold() for d in (original, raw) for k in ('notice_type', 'type')
                     if d.get(k) is not None]
            states = [str(d[k]).strip().casefold() for d in (original, raw) for k in ('status',)
                      if d.get(k) is not None]
            if not active or any(v not in {'yes', 'true', '1'} for v in active):
                gaps.append('Original active-state claims do not all establish an active notice')
            if not types or any(v not in {'solicitation', 'combined synopsis/solicitation'} for v in types):
                gaps.append('Original notice type is not a current solicitation')
            if any(v not in {'active', 'open'} for v in states):
                gaps.append('Original status is conflicting, closed or unrecognized')
            # Display fields cannot contradict the original identity/revision.
            for field, values in {
                'title': [original.get('title')],
                'agency': [original.get('agency'), raw.get('agency')],
                'office': [original.get('office'), raw.get('office')],
                'url': [original.get('api_url'), original.get('url'), raw.get('url'), raw.get('uiLink')],
            }.items():
                if record.get(field) and record[field] not in values:
                    gaps.append(f'Projected {field} disagrees with original source')
            if record.get('description') and not any(record['description'] in str(v or '') for v in
                    (description, original.get('description'), raw.get('description'), raw.get('description_snippet'))):
                gaps.append('Projected description is unsupported by original source')
            if (not family or family['order_status'] != 'ordered' or family['representative_id'] != sid):
                gaps.append('Notice is superseded or family chronology is unresolved')
            if not parent or parent.classification.value != 'bid_now' or parent.recommendation.value != 'pursue':
                gaps.append('No current bound Assess BID_NOW/PURSUE requirement approval')
            checked = deepcopy(original)
            checked['description'] = description
            if self.taxonomy is not None:
                support = requirement_support(checked, self.taxonomy)
            if support.get('requested_support') is not True:
                gaps.append('Original source does not establish requested capability work')
            if parent:
                evidence = next((e for e in parent.authoritative_evidence
                    if e.evidence_id == parent.requirement_review_evidence_id), None)
                observed = acquired_at(evidence) if evidence is not None else None
                timing = temporal_assessment(record_time(original, 'deadline'),
                    record_time({'retrieved_at': observed}, 'observed'), self.as_of,
                    posted=record_time(original, 'posted'))
                if (timing['response_window'] != 'open' or timing['source_freshness'] != 'fresh'
                        or not timing['evidence_within_cutoff']):
                    gaps.append('Current action clock or native acquisition is not established')
            if self.as_of is not None:
                action = response_instruction(description, self.as_of,
                    deadline=record_time(original, 'deadline')['selected']['raw'])
            if action.get('supported') is not True:
                gaps.append('No supported current source response instruction')
            route = access_decision(original, getattr(self, 'profile_json', {}).get('certifications') or [])
            if not route['eligible_route']:
                gaps.append(route['route_basis'])
        return dict(version=VERSION, admitted=not gaps, source_id=sid,
            source_record_sha256=digest(original) if original else None,
            run_id=self.run_id, context_sha256=self.fingerprint, gaps=list(dict.fromkeys(gaps)),
            requirement=support, temporal=timing, response_action=action, route=route,
            family=family)

    def seal_graph(self, payload):
        self._graph_sha256 = digest(payload)

    def accepts_graph(self, payload):
        return (not self.gaps and self.unchanged() and
                getattr(self, '_graph_sha256', None) == digest(payload))

    def receipt(self):
        return dict(version=VERSION, context_sha256=self.fingerprint, run_id=self.run_id,
            files=self.files, gaps=self.gaps, family_count=len(self.families),
            projection_diagnostics=getattr(self, 'projection_diagnostics', []),
            source_count=len(self.sources), unchanged=self.unchanged(),
            source_authenticity='bounded original-input and native Assess binding; not independent source authentication')
