"""Ordinary original-input bridge: bound positive control and independent refusals."""
from copy import deepcopy
import json

import pytest

from agents.assess import ledger
from agents.assess.approval import current_assess_binding
from agents.golden_press.evidence_pack_v2 import build_corrected_pack, canonicalize_requirement_families
from agents.golden_press.market_map_projection import attach_v2_targets
from agents.golden_press.notice_bridge import NoticeReadContext, access_decision
from agents.golden_press.retrieval import run_l1_from_sweep
from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm
from tests.test_assess_ledger import NOW, _profile, _sweep, _depth_record


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def original_fixture(root, mutate=None):
    profile = _profile()
    sweep = _sweep()
    sweep['results']['sam.gov'] = [sweep['results']['sam.gov'][0]]
    sweep['results']['sam_census']['matched'] = 1
    notice = sweep['results']['sam.gov'][0]
    notice['response_deadline'] = '2026-08-01T17:00:00Z'
    notice['raw_payload'].update(set_aside='Unrestricted')
    depth = _depth_record()
    depth['source_depth']['description'] += ' Submit proposals by August 1, 2026 at 17:00 UTC.'
    sweep['results']['dossiers'] = {'depth_records': [depth]}
    if mutate:
        mutate(sweep)
    review = root / 'data/review'
    sweep_path = root / 'data/cleaned/searches_testco.json'
    write(sweep_path, sweep)
    write(root / 'clients/testco/profile.json', profile.model_dump(mode='json'))
    taxonomy = CapabilityTaxonomy(client_name='Testco', version=1, updated='2026-07-10',
                                 core=[TaxonomyTerm(term='packet capture')])
    write(root / 'clients/testco/capability_taxonomy.json', taxonomy.model_dump(mode='json'))
    binding = current_assess_binding('Testco', review_dir=str(review), sweep_path=str(sweep_path),
                                    sweep=sweep, profile=profile)
    proposed, _, _ = ledger.build_assess_run('Testco', sweep, profile, binding, as_of=NOW)
    reviews = []
    for parent in proposed.live.records:
        if not parent.requirement_excerpt:
            continue
        evidence = next(e for e in parent.authoritative_evidence if parent.requirement_excerpt in e.excerpt)
        reviews.append(dict(notice_id=parent.notice_id, evidence_id=evidence.evidence_id,
            excerpt=parent.requirement_excerpt, capability_terms=['packet capture'], decision='approved',
            attachment_inventory_count=parent.attachment_inventory_count,
            attachment_inventory_hash=parent.attachment_inventory_hash,
            attachments_reviewed=False, reviewed_by='synthetic fixture reviewer', reviewed_at=NOW.isoformat()))
    review_data = dict(schema_version=1, client='Testco', binding=binding, reviews=reviews)
    write(review / 'testco.live_requirements.json', review_data)
    manifest = ledger.assess_projection_input_manifest('Testco', review_dir=review)
    run, diagnostics, index = ledger.build_assess_run('Testco', sweep, profile, binding,
        requirement_reviews_payload=review_data, projection_inputs=manifest, as_of=NOW)
    ledger.persist_assess_run(run, binding, diagnostics, index, manifest,
                             state_dir=root / 'data/state/assess_runs')
    return sweep


def graph_fixture(root, mutate=None):
    sweep = original_fixture(root, mutate)
    records, _ = run_l1_from_sweep(sweep, ['packet capture'])
    pack = dict(client_name='Testco', generated_at=NOW.isoformat(),
                records=[r.model_dump(mode='json') for r in records])
    pack_path = root / 'pack.json'
    write(pack_path, pack)
    context = NoticeReadContext(root, 'Testco', 'testco', NOW)
    _, graph = build_corrected_pack('testco', 'Testco', root=root,
        pressed_pack_path=pack_path, pack_dir=root / 'output', notice_context=context)
    return context, graph


def test_original_input_positive_survives_constructor_graph_and_native_projection(tmp_path):
    context, graph = graph_fixture(tmp_path)
    assert not context.gaps
    assert graph['qualified_opportunities'] == ['N1'], graph['held_opportunities']
    rows = attach_v2_targets((), graph, notice_context=context)
    assert len(rows) == 1 and rows[0].identifier == 'N1'
    assert rows[0].access_route == 'Bid directly.'
    assert graph['records'][0]['notice_admission']['requirement']['requested_support'] is True
    assert graph['graph_contract_certified']
    # Saved shape and booleans alone cannot reopen the population.
    assert attach_v2_targets((), graph) == ()
    changed = deepcopy(graph)
    changed['qualified_opportunity_records'][0]['title'] = 'Invented narrative'
    assert attach_v2_targets((), changed, notice_context=context) == ()


@pytest.mark.parametrize('mutation', [
    lambda s: s['results']['sam.gov'][0]['raw_payload'].update(active='No'),
    lambda s: s['results']['sam.gov'][0]['raw_payload'].update(type='Award Notification'),
    lambda s: s['results']['sam.gov'][0]['raw_payload'].update(set_aside='8(a)'),
    lambda s: s['results']['sam.gov'][0]['raw_payload'].pop('set_aside'),
    lambda s: s['results']['dossiers']['depth_records'][0]['source_depth'].update(description='No packet capture is required. Submit proposals by August 1, 2026 at 17:00 UTC.'),
    lambda s: s['results']['dossiers']['depth_records'][0]['source_depth'].update(description='The contractor shall provide packet capture.'),
    lambda s: s['results']['dossiers']['depth_records'][0]['source_depth'].update(retrieved_at='2026-07-09T11:00:00Z'),
    lambda s: s['results']['dossiers']['depth_records'][0]['source_depth'].pop('retrieved_at'),
    lambda s: s['results']['sam.gov'][0].update(response_deadline='2026-08-01'),
])
def test_one_missing_source_leg_withholds_and_preserves_diagnostics(tmp_path, mutation):
    context, graph = graph_fixture(tmp_path, mutation)
    assert not graph['qualified_opportunities']
    assert graph['records'] and graph['held_opportunities']
    assert attach_v2_targets((), graph, notice_context=context) == ()


def test_current_files_invalidate_a_warm_graph_without_reusing_saved_decisions(tmp_path):
    context, graph = graph_fixture(tmp_path)
    assert graph['qualified_opportunities'] == ['N1']
    path = tmp_path / 'data/cleaned/searches_testco.json'
    source = json.loads(path.read_text())
    source['results']['sam.gov'][0]['raw_payload']['active'] = 'No'
    write(path, source)
    assert attach_v2_targets((), graph, notice_context=context) == ()
    changed = NoticeReadContext(tmp_path, 'Testco', 'testco', NOW)
    assert changed.gaps and changed.fingerprint != context.fingerprint


def test_structured_families_keep_independent_calls_and_latest_closed_history():
    def row(rid, solicitation, posted, title):
        return dict(record_id=rid, source='sam.gov', solicitation=solicitation,
                    agency='Agency', office='Office', posted_date=posted, title=title)
    rows = [row('A', 'CALL-1', '2026-07-01', 'Identical title'),
            row('B', 'CALL-2', '2026-07-02', 'Identical title'),
            row('C', 'CALL-1', '2026-07-03', 'Renamed cancelled amendment')]
    result = canonicalize_requirement_families(rows)
    assert [r['record_id'] for r in result] == ['B', 'C']
    assert result[1]['family_member_ids'] == ['A', 'C']
    assert result[1]['family_members'][0]['superseded_by'] == 'C'
    assert rows[0]['title'] == 'Identical title'


def test_route_does_not_infer_access_from_contact_or_incumbent():
    assert not access_decision({'description': 'Incumbent: Example Inc', 'set_aside': '8(a)'}, [])['eligible_route']
    assert access_decision({'set_aside': '8(a)'}, ['8a'])['eligible_route']
    assert not access_decision({'set_aside': 'Unknown'}, ['8a'])['eligible_route']
