"""Bounded original award-detail retrieval for source-backed account research."""
from datetime import datetime, timezone
from copy import deepcopy
from tools.api._http import get_json
from tools.api.forecasts import strict_offline
from tools.text_match import matching_phrases


def enrich_forecast_contracts(payload, *, resolver=None, fetch=None, limit=6):
    """Resolve explicit forecast contract IDs; preserve order/parent separation.

    A failed lookup, a successful empty query and a capped query are distinct.
    Nothing here infers a follow-on opportunity from a historical award.
    """
    import re
    from tools.api.usaspending import resolve_award
    from tools.api._http import post_json
    from agents.assess.research_subjects import _locator
    identifiers = {}
    for candidate in payload.get('research_candidates') or []:
        row = candidate.get('record') or {}
        if hasattr(row, 'model_dump'):
            row = row.model_dump(mode='json')
        raw = row.get('source_fields') or {}
        for token in re.findall(r'\b[A-Z0-9]{10,24}\b', str(raw.get('contract_number') or row.get('predecessor_contract_id') or '').upper()):
            identifiers.setdefault(token, []).append(row.get('source_id'))
    attempts, records = [], []
    for number, (identifier, forecasts) in enumerate(identifiers.items()):
        if number >= limit or strict_offline() and resolver is None:
            attempts.append({'contract_id': identifier, 'state': 'not_run', 'reason': 'bounded lookup limit' if number >= limit else 'offline'})
            continue
        try:
            resolved = (resolver(identifier) if resolver else resolve_award(identifier,
                poster=lambda url, **kw: post_json(url, timeout=15, retries=1, **kw)))
            exact = [r for r in resolved if r.get('Award ID') == identifier]
            attempt = {'contract_id': identifier, 'forecast_ids': forecasts,
                       'state': 'success_empty' if not resolved else 'matched' if exact else 'identity_mismatch',
                       'returned_count': len(resolved), 'exact_count': len(exact)}
            attempts.append(attempt)
            for summary in exact[:2]:
                gid = summary.get('generated_internal_id') or summary.get('generated_unique_award_id')
                if not isinstance(gid, str) or not gid.startswith(('CONT_AWD_', 'CONT_IDV_')):
                    attempt.update(state='partial', detail_error='source returned no canonical award identity')
                    continue
                url = f'https://api.usaspending.gov/api/v2/awards/{gid}/'
                raw = fetch(url) if fetch else get_json(url, retries=1, timeout=20)
                if raw.get('generated_unique_award_id') != gid or raw.get('piid') != identifier:
                    raise ValueError('resolved award detail differs from exact contract identity')
                # IDV context is retained but never relabelled as an order.
                records.append({'contract_id': identifier, 'forecast_ids': forecasts,
                    'relationship': 'order' if gid.startswith('CONT_AWD_') else 'parent_vehicle',
                    'record': {**raw, 'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat()}})
        except Exception as exc:
            attempts.append({'contract_id': identifier, 'state': 'failed', 'error': str(exc)[:400]})
    return {'schema_version': 'forecast-award-joins.v1', 'records': records,
            'attempts': attempts, 'lookup_limit': limit,
            'boundary': 'Exact published contract identifiers; historical award context, no inferred follow-on'}


def enrich_buyer_research(buyer_map, profile, scope_agencies, *, fetch=None, limit=10):
    from tools.agencies import matches_record
    from agents.assess.research_subjects import _locator
    result=deepcopy(buyer_map)
    records, attempts=[],[]
    candidates={}
    selected=0
    requests=0
    # Exact client identity only. Generic cyber-services rows are not OEM buys.
    terms=[profile.client_name]
    for buyer in result.get('buyers') or []:
        agency=' '.join(filter(None,(buyer.get('agency'),buyer.get('buyer'))))
        if scope_agencies and not any(matches_record(agency,s) for s in scope_agencies): continue
        for row in buyer.get('records') or []:
            if row.get('kind')!='award' or not matching_phrases(row.get('description') or '',terms): continue
            selected += 1
            candidate={**row,'agency':buyer.get('agency'),'component':buyer.get('buyer')}
            try: system,sid,*_=_locator('award',candidate)
            except (TypeError,ValueError) as exc:
                attempts.append({'source_record_id':row.get('generated_internal_id'), 'supplied_award_id':row.get('award_id'), 'source_url':row.get('url'), 'status':'invalid', 'reason':str(exc)})
                continue
            candidates.setdefault(sid,candidate)
    for index,(sid,candidate) in enumerate(candidates.items()):
        if index>=limit or strict_offline() and fetch is None:
            attempts.append({'source_record_id':sid,'status':'not_run','reason':'bounded detail limit' if index>=limit else 'strict offline'})
            continue
        url=f'https://api.usaspending.gov/api/v2/awards/{sid}/'
        try:
            requests += 1
            raw=fetch(url) if fetch is not None else get_json(url,retries=1)
            if not isinstance(raw,dict) or raw.get('generated_unique_award_id')!=sid:
                raise ValueError('award detail identity mismatch')
            row={**raw,'url':url,'retrieved_at':datetime.now(timezone.utc).isoformat()}
            _locator('award',row)
            records.append(row)
            attempts.append({'source_record_id':sid,'status':'returned','source_url':url})
        except Exception as exc:
            attempts.append({'source_record_id':sid,'status':'failed','reason':str(exc)[:200]})
    result['research_award_details']={'selected_rows':selected,'unique_valid_candidates':len(candidates),'detail_requests':requests,'counts':{status:sum(a['status']==status for a in attempts) for status in ['returned','invalid','failed','not_run']},'schema_version':1,'records':records,'attempts':attempts,'limit':limit,'purpose':'account research; no funded continuation inferred'}
    return result
