"""S4-SOURCE-CLOCK-001: source authority and archival compatibility, offline."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agents.assess.contracts import AssessRun, EvidenceRef, LiveClassification
from agents.assess.ledger import (build_assess_run, persist_assess_run, load_current_assess_run,
    assess_client_storage_key, _digest, _legacy_projection, _run_identity, _run_projection)
from agents.assess.source_clock import acquired_at, acquisition_clock, acquisition_summary, SourceAcquisition
from agents.leadgen import draft_lead_rows, run_press
from agents.leadgen.export_assess import export_current_assess_run
from agents.leadgen.schema_export import json_schemas
from tests.test_assess_ledger import (_sweep, _depth_record, _profile, _build,
    _requirement_review_payload, _build_with_requirement_review, NOW, BINDING,
    _horizon_payload, _add_partner_edge, _partner_document)


def sweep_with_depth(stamp="2026-07-10T05:00:00-06:00"):
    sweep = _sweep()
    depth = _depth_record()
    depth['source_depth']['retrieved_at'] = stamp
    sweep['results']['dossiers'] = {'records': [depth]}
    return sweep


def evidence(run, nid='N1'):
    return next(r for r in run.live.records if r.notice_id == nid).authoritative_evidence[-1]


def test_reapproval_changes_assessment_clock_not_acquisition_or_ids(tmp_path):
    sweep = sweep_with_depth()
    reviews = _requirement_review_payload(sweep)
    snapshots = []
    for at in (NOW, NOW + timedelta(days=1)):
        run, diagnostics, index = build_assess_run('Testco', sweep, _profile(), BINDING,
            as_of=at, requirement_reviews_payload=reviews,
            approval_payload={**BINDING, 'binding': BINDING, 'client': 'Testco',
                'approved_by': 'operator', 'approved_at': at.isoformat()})
        # The exact original requirement review still passes on the second assessment.
        assert next(r for r in run.live.records if r.notice_id == 'N1').classification == LiveClassification.BID_NOW
        persist_assess_run(run, BINDING, diagnostics, index, state_dir=tmp_path)
        exported = export_current_assess_run('Testco', scope='all', state_dir=tmp_path)
        restored = AssessRun.model_validate(exported['run'])
        assert evidence(restored).source_acquisition.raw_values == ('2026-07-10T05:00:00-06:00',)
        assert acquired_at(evidence(restored)) == datetime(2026,7,10,11,tzinfo=timezone.utc)
        drafts = draft_lead_rows(restored)
        assert len(drafts.parents) == 3
        assert sum(len(r.authoritative_evidence) for r in restored.live.records) == 3
        receipt = run_press(assess=exported)
        from agents.leadgen.press_html import render_html
        html = render_html(receipt)
        assert 'Original timestamp: 2026-07-10T05:00:00-06:00' in html
        assert 'Collection time not established' in html
        snapshots.append([(e.evidence_id, e.retrieved_at, e.source_acquisition)
                          for r in restored.live.records for e in r.authoritative_evidence])
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize('stamp,status', [
    (None,'missing'), ('garbage','invalid'), ('2026-07-10','date_only'),
    ('2026-07-10T11:00:00','unknown_timezone'), ('2026-07-10T11:00:00-00:00','unknown_timezone'),
    (20260710,'invalid'), (datetime(2026,7,10,tzinfo=timezone.utc),'invalid')])
def test_unknown_depth_clock_never_turns_into_assessment_time(stamp,status):
    sweep = sweep_with_depth(stamp)
    sweep['results']['sam.gov'][0]['retrieved_at'] = NOW.isoformat()
    run, _, _ = _build_with_requirement_review(sweep)
    e = evidence(run)
    assert e.retrieved_at is None
    assert e.source_acquisition.status == status
    record = next(r for r in run.live.records if r.notice_id == 'N1')
    assert record.classification == LiveClassification.UNSCREENED
    assert record.requirement_reviewed_at is None
    assert record.verified_at == NOW
    assert len(run.live.records) == 3


def test_unbound_row_or_wrong_notice_depth_cannot_grant_acquisition():
    for wrong in (False, True):
        sweep = sweep_with_depth() if wrong else _sweep()
        sweep['results']['sam.gov'][0]['raw_payload']['retrieved_at'] = NOW.isoformat()
        if wrong:
            sweep['results']['dossiers']['records'][0]['source_depth']['notice_id'] = 'WRONG'
        run, _, _ = _build(sweep)
        assert acquired_at(evidence(run)) is None
        assert evidence(run).source_acquisition.status == 'untrusted'


def test_duplicate_bound_depth_clock_conflict_is_retained_not_last_wins():
    sweep = sweep_with_depth()
    earlier = copy.deepcopy(sweep['results']['dossiers']['records'][0])
    earlier['source_depth']['retrieved_at'] = '2026-07-09T11:00:00Z'
    sweep['results']['dossiers']['depth_records'] = [earlier]
    run, _, _ = _build_with_requirement_review(sweep)
    assert evidence(run).source_acquisition.status == 'conflict'
    assert evidence(run).retrieved_at is None
    assert len(run.live.records) == 3


def test_equal_offset_instants_reconcile_and_typed_json_consumers_agree():
    sweep = sweep_with_depth()
    duplicate = copy.deepcopy(sweep['results']['dossiers']['records'][0])
    duplicate['source_depth']['retrieved_at'] = '2026-07-10T11:00:00Z'
    sweep['results']['dossiers']['depth_records'] = [duplicate]
    run, _, _ = _build(sweep)
    e = evidence(run)
    assert acquired_at(e) == acquired_at(e.model_dump(mode='json'))
    assert e.source_acquisition.status == 'known'
    assert len(e.source_acquisition.raw_values) == 2


def test_fresh_row_cannot_freshen_description_that_predates_posting():
    sweep = sweep_with_depth('2026-06-30T11:00:00Z')
    sweep['results']['sam.gov'][0]['retrieved_at'] = NOW.isoformat()
    run, _, _ = _build_with_requirement_review(sweep)
    assert evidence(run).retrieved_at == datetime(2026,6,30,11,tzinfo=timezone.utc)
    assert next(r for r in run.live.records if r.notice_id == 'N1').classification == LiveClassification.UNSCREENED


def test_forged_known_status_binding_or_copy_is_refused():
    run, _, _ = _build(sweep_with_depth())
    e = evidence(run)
    for change in ({'retrieved_at': NOW}, {'record_hash': 'b'*64}):
        forged = e.model_copy(update=change)
        assert acquired_at(forged) is None
        with pytest.raises(ValidationError):
            EvidenceRef.model_validate(forged.model_dump(mode='python'))
    payload = e.source_acquisition.model_dump()
    payload.update(raw_values=['not a time'])
    with pytest.raises(ValidationError):
        SourceAcquisition.model_validate(payload)
    altered = run.model_dump(mode='json')
    altered['live']['records'][0]['authoritative_evidence'][0]['source_acquisition'] = None
    with pytest.raises(ValidationError, match='requires explicit'):
        AssessRun.model_validate(altered)


def test_strict_unknown_facts_cannot_inherit_sweep_date_and_known_still_passes():
    from agents.reports.facts import _ledger_monitor_facts, _Counter, _stamp_provenance, FactPack
    from agents.reports.verification import freshness_violations, data_current_date
    run, _, _ = _build(sweep_with_depth())
    monitor = next(r for r in run.live.records if r.notice_id == 'N3')
    view = SimpleNamespace(notices=[SimpleNamespace(record=monitor,current={})])
    # Actual strict monitor projection, then normal shared stamping.
    facts = _ledger_monitor_facts(view, _Counter())
    _stamp_provenance(facts, NOW + timedelta(days=2))
    assert facts and facts[0].retrieved_at is None
    from agents.reports.facts import Fact
    facts=[Fact.model_validate(f.model_dump(mode='json')) for f in facts]
    _stamp_provenance(facts, NOW + timedelta(days=3))
    assert facts[0].retrieved_at is None
    pack = FactPack(client_name='Testco',as_of=NOW.date(),facts=facts)
    assert data_current_date(pack,[facts[0].id]) is None
    assert freshness_violations(pack,[facts[0].id],now=NOW)[0].rule == 'FRESHNESS_UNKNOWN'
    from agents.reports.facts import _ledger_opportunity_facts
    approved,_,_ = _build_with_requirement_review(sweep_with_depth())
    live = next(r for r in approved.live.records if r.notice_id=='N1')
    facts = _ledger_opportunity_facts(SimpleNamespace(actionable=[SimpleNamespace(record=live,current={})]),_Counter())
    _stamp_provenance(facts, NOW+timedelta(days=2))
    assert facts[0].retrieved_at == acquired_at(evidence(approved))
    pack = FactPack(client_name='Testco',as_of=NOW.date(),facts=facts)
    assert freshness_violations(pack,[facts[0].id],now=NOW) == []


def test_family_completeness_preserves_unknown_member_and_no_generated_fallback():
    from agents.golden_press.external_product_projection import build_external_product_document
    from tests.test_external_product_projection import _graph,_market_map,_pack
    run,_,_ = _build(sweep_with_depth())
    e = [evidence(run), evidence(run,'N2')]
    summary = acquisition_summary(e)
    assert summary == {'status':'incomplete','known':1,'total':2,
        'oldest_known_at':'2026-07-10T11:00:00+00:00','source_as_of':None}
    graph = _graph(); graph['records'][0]['lane']='L1_notice'; nid=graph['records'][0]['record_id']
    doc = build_external_product_document(market_map=_market_map(),graph_payload=graph,
        evidence_pack=_pack(), profile={},client_name='Acme',slug='acme',as_of='2026-09-08',
        leadgen={'evidence_clocks':{nid:summary}})
    row=next(r for r in doc.slots[4].records if r['source_id']==nid)
    assert row['source_as_of'] is None
    assert row['source_clock_coverage'] == '1 of 2 evidence acquisition times recorded'


def legacy_artifact(tmp_path):
    run, diagnostics, index = _build_with_requirement_review(sweep_with_depth())
    path=persist_assess_run(run,BINDING,diagnostics,index,state_dir=tmp_path)
    payload=json.loads(path.read_text()); root=path.parent
    raw=_legacy_projection(payload['run'])
    for record in raw['live']['records']:
        for e in record['authoritative_evidence']:
            if e['retrieved_at'] is None: e['retrieved_at']=NOW.isoformat()
    raw['run_id']='legacy-read'
    for lane in ('live','horizon','partners'): raw[lane]['run_id']='legacy-read'
    old=AssessRun.model_validate(raw)
    projection=_legacy_projection(_run_projection(as_of=old.as_of,scope=old.scope,
        profile_version=old.profile_version,live=old.live,horizon=old.horizon,partners=old.partners,
        coverage=old.coverage,approval_status=old.approval_status,approved_by=old.approved_by,
        approved_at=old.approved_at,partial_release_approved=old.partial_release_approved,
        requires_human_review=old.requires_human_review,projection_inputs=payload['projection_inputs'],
        diagnostics=tuple(payload['diagnostics']),posting_index=payload['posting_index']))
    rid=_run_identity(old.client_name,BINDING,projection,schema_version=1)
    raw['run_id']=rid
    for lane in ('live','horizon','partners'):raw[lane]['run_id']=rid
    payload.update(schema_version=1,run=raw,identity={'algorithm':'sha256-canonical-json-v1','projection_sha256':_digest(projection)})
    old_path=root/(rid.rsplit(':',1)[-1]+'.json'); old_path.write_text(json.dumps(payload))
    pointer={'schema_version':1,'run_id':rid,'artifact':old_path.name,'artifact_sha256':_digest(payload)}
    (root/'all.current.json').write_text(json.dumps(pointer))
    return old_path,root/'all.current.json',payload


def test_legacy_positive_reads_with_original_identity_but_no_new_source_authority(tmp_path):
    path,pointer,payload=legacy_artifact(tmp_path)
    before=(path.read_bytes(),pointer.read_bytes())
    loaded=load_current_assess_run('Testco','all',state_dir=tmp_path)
    assert loaded == payload
    run=AssessRun.model_validate(loaded['run'])
    assert any(r.classification==LiveClassification.BID_NOW for r in run.live.records)
    assert acquired_at(evidence(run)) is None
    assert (path.read_bytes(),pointer.read_bytes()) == before
    assert not run.can_release()


@pytest.mark.parametrize('attack',['inject','downgrade'])
def test_legacy_wrapper_cannot_smuggle_v2_provenance(tmp_path,attack):
    if attack=='inject':
        path,pointer,payload=legacy_artifact(tmp_path)
        payload['run']['live']['records'][0]['authoritative_evidence'][0]['source_acquisition']=None
    else:
        run,d,i=_build(sweep_with_depth());path=persist_assess_run(run,BINDING,d,i,state_dir=tmp_path)
        pointer=path.parent/'all.current.json';payload=json.loads(path.read_text());payload['schema_version']=1
    path.write_text(json.dumps(payload));p=json.loads(pointer.read_text());p.update(schema_version=1,artifact_sha256=_digest(payload));pointer.write_text(json.dumps(p))
    assert load_current_assess_run('Testco','all',state_dir=tmp_path) is None


def test_generated_pathway_and_lead_schemas_admit_null_and_provenance():
    for name in ('ActionableExternalPathway','LeadRow'):
        props=json_schemas()[name]['$defs']['EvidenceRef']['properties']
        assert {'type':'null'} in props['retrieved_at']['anyOf']
        assert 'source_acquisition' in props


def test_partner_clock_unknown_and_horizon_known_not_assessment_time():
    sweep=sweep_with_depth();_add_partner_edge(sweep)
    run,_,_=build_assess_run('Testco',sweep,_profile(),BINDING,as_of=NOW,
        horizon_payload=_horizon_payload(),document=_partner_document(),
        requirement_reviews_payload=_requirement_review_payload(sweep))
    assert run.horizon.items
    assert all(acquired_at(e) is not None for t in run.horizon.items for e in t.evidence)
    assert run.partners.items
    assert all(e.retrieved_at is None for p in run.partners.items for e in p.evidence)


def test_base_serialized_positive_leads_remain_history_but_cannot_be_requalified():
    from pathlib import Path
    from agents.leadgen.contracts import LeadRow, OpportunityAssessment
    from agents.leadgen.enums import LeadTier
    from agents.leadgen.qualify import _qualify_one
    from agents.leadgen.eval.load import load_score_input
    fixture = json.loads((Path(__file__).parent/'fixtures/assess_source_clock_legacy_leads.json').read_text())
    assert fixture['base_commit'] == 'ca6620dd9a4387b5145e49588a878ca3efb925e4'
    parent = OpportunityAssessment.model_validate(fixture['parent'])
    for serialized in fixture['leads']:
        assert all('source_acquisition' not in e for e in serialized['external_pathway']['evidence'])
        old = LeadRow.model_validate(serialized)
        assert old.lead_tier in (LeadTier.LEAD_T1,LeadTier.LEAD_T2)
        assert load_score_input({'parents':[fixture['parent']],'leads':[serialized]}).leads == (old,)
        current, _, promoted = _qualify_one(old,parent,None,(),{},None,t1_used=False)
        assert current.lead_tier == LeadTier.HOLD and not promoted
        assert current.lead_id == old.lead_id
        assert current.external_pathway.evidence == old.external_pathway.evidence
        assert 'source acquisition chronology' in current.next_action.blocked_by


def test_legacy_reviewed_case_history_survives_unknown_chronology_projection():
    from agents.assess.reviewed_cases import current_case_record
    from tests.test_operating_repair import _book
    book = _book(); before = book.model_dump(mode='json')
    for case in book.cases:
        row = current_case_record(case.record)
        assert row.notice_id == case.record.notice_id
        assert row.verified_at == case.record.verified_at
        assert len(row.authoritative_evidence) == len(case.record.authoritative_evidence)
        assert [e.evidence_id for e in row.authoritative_evidence] == [e.evidence_id for e in case.record.authoritative_evidence]
        assert all(e.retrieved_at is None for e in row.authoritative_evidence)
        assert row.requirement_reviewed_at is None
        if case.record.requirement_reviewed_at or case.record.attachment_reviewed_at:
            assert 'Retained historical review' in ' '.join(row.fit_trace)
        assert current_case_record(row) == row
    assert book.model_dump(mode='json') == before


def test_export_envelope_downgrade_rejected_before_mapping(tmp_path):
    from agents.leadgen.from_assess import coerce_assess_run
    run,d,index=_build(sweep_with_depth())
    persist_assess_run(run,BINDING,d,index,state_dir=tmp_path)
    exported=export_current_assess_run('Testco',scope='all',state_dir=tmp_path)
    assert coerce_assess_run(exported).run_id == run.run_id
    # Native export has a source record rather than an immutable schema wrapper;
    # deliberately wrapping v2 content as schema1 must refuse.
    with pytest.raises(ValueError,match='version'):
        coerce_assess_run({'schema_version':1,'run':exported['run']})


@pytest.mark.parametrize('stamp',[None,'2026-07-10','2026-07-10T11:00:00','2026-07-10T11:00:00-00:00',20260710])
def test_horizon_keeps_existing_strict_refusal_for_unknown_source_time(stamp):
    payload=_horizon_payload()
    payload['fact_bank'][0]['retrieved_at']=stamp
    run,diagnostics,_=build_assess_run('Testco',sweep_with_depth(),_profile(),BINDING,
        as_of=NOW,horizon_payload=payload)
    assert not run.horizon.items
    assert any('Horizon' in d for d in diagnostics)
    assert len(run.live.records)==3


def test_already_held_renewal_retains_named_acquisition_gap_and_prior_blocker():
    from tests.test_leadgen_qualify import _holder_partner,_decision_actions
    from tests.test_leadgen_from_assess import _run
    from agents.leadgen import qualify_drafts
    from agents.leadgen.enums import LeadTier,TIER_READINESS
    run=_run(partners=[_holder_partner()]);actions=_decision_actions()
    batch=draft_lead_rows(run,actions)
    assert {l.lead_tier for l in qualify_drafts(batch,run,actions).leads} == {LeadTier.LEAD_T2}
    old=batch.leads[0]
    lead=old.model_copy(update={'lead_tier':LeadTier.HOLD,'readiness':TIER_READINESS[LeadTier.HOLD],
        'next_action':old.next_action.model_copy(update={'blocked_by':'Retained prior question'}),
        'external_pathway':old.external_pathway.model_copy(update={'evidence':tuple(
            e.model_copy(update={'source_acquisition':None}) for e in old.external_pathway.evidence)})})
    held=qualify_drafts(batch.model_copy(update={'leads':(lead,)}),run,actions).leads[0]
    assert held.lead_tier==LeadTier.HOLD
    assert 'source acquisition chronology' in held.next_action.blocked_by
    assert 'Retained prior question' in held.next_action.blocked_by


def test_report_label_only_calls_bound_source_clock_acquisition():
    from agents.golden_press.external_product_render import _fields
    generic=_fields({'source_as_of':'2026-07-10'})
    assert 'Evidence date' in generic and 'Source acquisition' not in generic
    bound=_fields({'source_as_of':'2026-07-10','source_clock_status':'complete'})
    assert 'Source acquisition' in bound


def test_duplicate_depth_missing_selected_clock_cannot_borrow_other_snapshot_time():
    sweep=sweep_with_depth(None)
    other=copy.deepcopy(sweep['results']['dossiers']['records'][0])
    other['source_depth']['retrieved_at']='2026-07-10T11:00:00Z'
    sweep['results']['dossiers']['depth_records']=[other]
    run,_,_=_build_with_requirement_review(sweep)
    assert evidence(run).source_acquisition.status=='conflict'
    assert evidence(run).retrieved_at is None
    assert len(run.live.records)==3


@pytest.mark.parametrize('form',['raw','typed','ordinary_export'])
def test_explicit_legacy_run_relabel_cannot_carry_v2_acquisition(form,tmp_path):
    from agents.leadgen.from_assess import coerce_assess_run
    run,d,index=_build(sweep_with_depth())
    assert coerce_assess_run(run)==run
    raw=run.model_dump(mode='json'); legacy_id=raw['run_id'].replace('assess:v2:','assess:v1:')
    raw['run_id']=legacy_id
    for lane in ('live','horizon','partners'):raw[lane]['run_id']=legacy_id
    if form=='raw': payload=raw
    elif form=='typed':
        payload=run.model_copy(update={'run_id':legacy_id,**{lane:getattr(run,lane).model_copy(
            update={'run_id':legacy_id}) for lane in ('live','horizon','partners')}})
    else:
        persist_assess_run(run,BINDING,d,index,state_dir=tmp_path)
        payload=export_current_assess_run('Testco',scope='all',state_dir=tmp_path)
        payload['run']=raw
    with pytest.raises(ValidationError,match='legacy v1'):
        coerce_assess_run(payload)
