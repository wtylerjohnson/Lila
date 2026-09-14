"""Pure, source-bound investigation projection from ordinary sweep producers.

Source payloads remain intact. Their captured retrieval claims are untrusted
until a separately sanctioned source acquisition contract establishes them.
No reviewer, future event, live identity or buying motion is manufactured.
"""
from __future__ import annotations
import hashlib
from datetime import date, datetime, timezone
import json
from urllib.parse import urlsplit, unquote
from agents.assess.source_clock import acquisition_clock
from agents.assess.contracts import ResearchSubject, ResearchSubjectLedger, EvidenceRef, EvidenceKind, EvidenceTier, EvidenceUse
from tools.text_match import matching_phrases


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def subject_identity(system, source_id):
    return 'research:v1:' + digest([system, source_id])


def _locator(kind, row):
    if kind == 'forecast':
        system, source_id, url = row.get('source'), row.get('source_id'), row.get('url')
        if system == 'dhs_apfs':
            raw = row.get('source_fields') or {}
            if source_id != str(raw.get('apfs_number') or raw.get('id') or ''):
                raise ValueError('forecast published identity disagrees with source row')
            from tools.api.forecasts.dhs_apfs import map_record
            mapped = map_record(raw, retrieved_at=datetime(1970,1,1,tzinfo=timezone.utc)).model_dump(mode="json")
            for field,value in mapped.items():
                # Older immutable forecast payloads predate these additive fields.
                # If supplied, they must still match the retained original source.
                if field in {'contacts', 'contact_publication_date'} and field not in row:
                    continue
                if field not in {'retrieved_at','first_seen','last_seen','record_hash'} and row.get(field) != value:
                    raise ValueError(f'forecast mapped field disagrees with original APFS source: {field}')
            expected = f"/record/{raw.get('id')}/public-print/"
            if urlsplit(str(url)).hostname != 'apfs-cloud.dhs.gov' or urlsplit(str(url)).path != expected:
                raise ValueError('forecast URL disagrees with captured record ID')
        agency, component = row.get('agency'), row.get('component')
        title, text = row.get('title'), row.get('description') or row.get('title')
    else:
        system = 'usaspending.gov'
        source_id = row.get('generated_unique_award_id') or row.get('generated_internal_id')
        piid = row.get('piid') or row.get('award_id')
        if not isinstance(source_id, str) or not isinstance(piid, str) or not source_id.startswith(f'CONT_AWD_{piid}_'):
            raise ValueError('award generated identity disagrees with PIID')
        url = row.get('url') or f'https://www.usaspending.gov/award/{source_id}'
        parts = urlsplit(url)
        expected = f'/award/{source_id}' if parts.hostname == 'www.usaspending.gov' else f'/api/v2/awards/{source_id}/'
        if parts.hostname not in {'www.usaspending.gov','api.usaspending.gov'} or unquote(parts.path) != expected:
            raise ValueError('award URL disagrees with captured identity')
        agency = row.get('awarding_agency') or row.get('agency')
        component = row.get('awarding_sub_agency') or row.get('component')
        if isinstance(agency,dict):
            component=(agency.get('subtier_agency') or {}).get('name')
            agency=(agency.get('toptier_agency') or {}).get('name')
        title = f"Award {piid}: {row.get('description') or 'scope not supplied'}"
        text = row.get('description')
    parts = urlsplit(str(url or ''))
    if parts.scheme != 'https' or not (parts.hostname or '').endswith(('.gov','.mil')) or parts.username or parts.password:
        raise ValueError('research requires an official government record URL')
    if not all(isinstance(v,str) and v.strip() for v in (system,source_id,agency,title,text)):
        raise ValueError('research source lacks identity, agency or description')
    return system,source_id,url,agency,component,title,text


def validate_subject(subject):
    if subject.discovery_context_json is not None:
        context=json.loads(subject.discovery_context_json)
        if not isinstance(context,dict):
            raise ValueError('research discovery context must be a JSON object')
    if subject.reviewed_overlay_json is not None:
        from agents.assess.reviewed_cases import ReviewedSubject
        overlay = ReviewedSubject.model_validate_json(subject.reviewed_overlay_json)
        if overlay.subject_id != subject.subject_id or overlay.source_sha256 != subject.source_sha256:
            raise ValueError("reviewed research differs from current source subject")
    row=json.loads(subject.source_payload_json)
    if canonical(row) != subject.source_payload_json or digest(row) != subject.source_sha256:
        raise ValueError('research source payload hash or canonical serialization differs')
    system,sid,url,agency,component,title,text=_locator(subject.source_kind,row)
    if (subject.source_system,subject.source_record_id,str(subject.source_url),subject.agency,subject.component,subject.title) != (system,sid,url,agency,component,title):
        raise ValueError('research fields disagree with original source record')
    if subject.subject_id != subject_identity(system,sid):
        raise ValueError('research subject identity disagrees with source')
    kind=EvidenceKind.AWARD if subject.source_kind=='award' else EvidenceKind.AGENCY_FORECAST
    expected_clock=acquisition_clock(row.get('retrieved_at'),basis='none',component='research_record',field='source_row.retrieved_at',binding=subject.source_sha256)
    if len(subject.evidence) != 1:
        raise ValueError('research subject requires its single original source record')
    for e in subject.evidence:
        expected_tier=EvidenceTier.MARKET if subject.source_kind=='award' else EvidenceTier.PROGRAM
        if e.evidence_id != 'ev:research:v1:'+subject.source_sha256 or e.tier != expected_tier or e.supports != (EvidenceUse.BUYER,) or e.source_name != system or e.observed_date is not None or e.effective_date is not None:
            raise ValueError('research source evidence cannot acquire additional authority')
        if e.source_acquisition != expected_clock or e.retrieved_at is not None:
            raise ValueError('research acquisition must retain the exact untrusted source claim')
        if e.kind != kind or str(e.source_url)!=url or e.record_hash!=subject.source_sha256 or e.excerpt!=text or not e.primary_source:
            raise ValueError('research evidence is not bound to this source record')


def source_posture(kind, row, as_of=None):
    if kind == 'forecast':return 'forecast_plan'
    period=row.get('period_of_performance') or {}
    try:
        ends={date.fromisoformat(str(v)[:10]) for v in (period.get('end_date'),row.get('end_date'),row.get('completion')) if v}
        starts={date.fromisoformat(str(v)[:10]) for v in (period.get('start_date'),row.get('start_date')) if v}
        if as_of is None or len(ends)!=1 or len(starts)>1:return 'award_timing_unknown'
        end_date=next(iter(ends))
        start_date=next(iter(starts)) if starts else None
        if start_date and start_date>end_date:return 'award_timing_unknown'
        if end_date<as_of.date():return 'historical_award'
        if start_date is None or start_date>as_of.date():return 'award_timing_unknown'
        return 'current_period_award'
    except (TypeError,ValueError):return 'award_timing_unknown'


def make_subject(kind, row, *, questions=(), route=None, discovery_context=None, as_of=None):
    system,sid,url,agency,component,title,text=_locator(kind,row)
    sha=digest(row)
    clock=acquisition_clock(row.get('retrieved_at'),basis='none',component='research_record',field='source_row.retrieved_at',binding=sha)
    unknowns=tuple(questions) or ('Current acquisition or continuation decision','Actual OEM and covered products','Government tool owner and purchasing authority','Eligible supplier and permitted OEM role')
    next_ask = (f'Is {title} still planned, and which current requirements identify the products, technical owner and eligible supplier?'
                if kind == 'forecast' else
                f'Who owns the next decision for {row.get("piid") or row.get("award_id")}, which products are covered, and is there a separate need beyond the recorded contract?')
    return ResearchSubject(subject_id=subject_identity(system,sid),source_kind=kind,source_posture=source_posture(kind,row,as_of),source_system=system,source_record_id=sid,source_url=url,title=title,agency=agency,component=component,source_payload_json=canonical(row),source_sha256=sha,
        discovery_context_json=canonical(discovery_context) if discovery_context else None,
        evidence=(EvidenceRef(evidence_id='ev:research:v1:'+sha,tier=EvidenceTier.PROGRAM if kind=='forecast' else EvidenceTier.MARKET,kind=EvidenceKind.AGENCY_FORECAST if kind=='forecast' else EvidenceKind.AWARD,source_name=system,source_url=url,source_acquisition=clock,record_hash=sha,excerpt=text,primary_source=True,supports=(EvidenceUse.BUYER,)),),
        open_questions=unknowns, next_ask=next_ask,route_hypothesis=route or 'Acquisition route remains to be established')


def build_research_ledger(searches, profile, *,run_id,client_name,profile_version,scope,as_of):
    from agents.assess.ledger import _agency_is_in_scope
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm, KillRule
    from tools.relevance.engine import score_record
    results=searches.get('results') or {}
    candidates=[]
    for row in ((results.get('incumbent_buyer_map') or {}).get('research_award_details') or {}).get('records') or []:
        if isinstance(row,dict): candidates.append(('award',row,{}))
    for c in (results.get('forecast_signals') or {}).get('research_candidates') or []:
        if isinstance(c,dict) and c.get('schema_version')==1 and isinstance(c.get('record'),dict):
            candidates.append(('forecast',c['record'],c))
    for row in (results.get('contract_awards') or {}).get('recompetes') or []:
        if isinstance(row,dict): candidates.append(('award',row,{}))
    for buyer in (results.get('incumbent_buyer_map') or {}).get('buyers') or []:
        for row in buyer.get('records') or []:
            if isinstance(row,dict) and row.get('kind')=='award':
                candidates.append(('award',{**row,'agency':buyer.get('agency'),'component':buyer.get('buyer')},{}))
    terms=getattr(profile, 'capability_terms', None)
    taxonomy=CapabilityTaxonomy(client_name=client_name,version=1,updated='1970-01-01',
        core=[TaxonomyTerm(term=t) for t in getattr(terms,'core',[])],
        exclude=[KillRule(term=t,scope='span') for t in getattr(terms,'excluded',[])])
    brand_terms=[client_name.strip()] if client_name.strip() else []
    output={}; diagnostics=[]
    for kind,row,context in candidates:
        try:
            system,sid,url,agency,component,title,text=_locator(kind,row)
            if not _agency_is_in_scope(' '.join(filter(None,(agency,component))),scope): continue
            if kind=='award' and not (matching_phrases(text,brand_terms) or taxonomy is not None and score_record(row,taxonomy).relevant): continue
            subject=make_subject(kind,row,questions=context.get('unknowns') or (),route=context.get('route_hypothesis'),discovery_context={k:v for k,v in context.items() if k!='record'},as_of=as_of)
            if subject.subject_id in output and output[subject.subject_id].source_sha256 != subject.source_sha256:
                diagnostics.append(f'Research source {sid} has multiple differing rows; first source retained and full sweep preserved')
            output.setdefault(subject.subject_id,subject)
        except (TypeError,ValueError,KeyError) as exc:
            diagnostics.append('Research source withheld: '+str(exc))
    return ResearchSubjectLedger(run_id=run_id,client_name=client_name,profile_version=profile_version,scope=scope,as_of=as_of,items=tuple(output.values())),diagnostics


def apply_reviewed_subjects(ledger, book, scope_designator):
    if not book.subjects:
        return ledger
    if book.client_name.casefold() != ledger.client_name.casefold() or book.scope_designator != scope_designator:
        raise ValueError("reviewed subjects differ from current client/scope")
    subjects = {s.subject_id:s for s in ledger.items}
    for overlay in book.subjects:
        subject=subjects.get(overlay.subject_id)
        if subject is None or subject.source_sha256 != overlay.source_sha256:
            raise ValueError("reviewed subject is absent or changed; refresh research")
        subjects[subject.subject_id]=ResearchSubject.model_validate({**subject.model_dump(mode="json"),"reviewed_overlay_json":canonical(overlay.model_dump(mode="json"))})
    return ResearchSubjectLedger.model_validate({**ledger.model_dump(mode="python"),"items":tuple(subjects.values())})
