"""Bounded original award-detail retrieval for source-backed account research."""
from datetime import datetime, timezone
from copy import deepcopy
from tools.api._http import get_json
from tools.api.forecasts import strict_offline
from tools.text_match import matching_phrases


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
