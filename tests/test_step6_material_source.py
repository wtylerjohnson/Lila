"""Material facts survive only through the bound original notice into products."""
from dataclasses import asdict
import json

import pytest

from agents.assess import ledger
from agents.golden_press.evidence_pack_v2 import build_corrected_pack
from agents.golden_press.market_map_projection import attach_v2_targets
from agents.golden_press.notice_bridge import NoticeReadContext
from agents.golden_press.retrieval import run_l1_from_sweep
from tests.test_native_notice_bridge import NOW, graph_fixture, original_fixture, write


def test_constructor_contacts_buyer_value_and_incumbent_do_not_become_source_facts(tmp_path):
    sweep = original_fixture(tmp_path)
    records, _ = run_l1_from_sweep(sweep, ['packet capture'])
    row = records[0].model_dump(mode='json')
    invented = 'Constructor-only'
    row.update(contact_name=invented, contact_title=invented,
        contact_email='unsourced@example.invalid', contact_phone='555-0100',
        contact_secondary_email='secondary@example.invalid', small_business_poc=invented,
        source_notice_contacts=[dict(name=invented, email='unsourced@example.invalid')],
        sub_agency=invented, ceiling_dollars=990000000, estimated_value_range=invented,
        recipient=invented, obligated_dollars=990000000, vehicle=invented,
        parent_award_id=invented, competition=invented, incumbent_name=invented,
        incumbent_basis=invented, route_organization=invented,
        relationship_provenance={'commercial_route': {'evidence': invented}})
    write(tmp_path / 'pack.json', dict(client_name='Testco', records=[row]))
    context = NoticeReadContext(tmp_path, 'Testco', 'testco', NOW)
    _, graph = build_corrected_pack('testco', 'Testco', root=tmp_path,
        pressed_pack_path=tmp_path / 'pack.json', pack_dir=tmp_path / 'output', notice_context=context)
    assert graph['qualified_opportunities'] == ['N1']
    facts = graph['qualified_opportunity_records'][0]
    assert facts['published_value'] is None and facts['sub_agency'] is None
    mm = [asdict(r) for r in attach_v2_targets((), graph, notice_context=context)]
    assert len(mm) == 1
    positive = json.dumps(dict(facts=facts, map=mm, records=graph['records']), default=str)
    assert invented not in positive
    assert 'unsourced@example.invalid' not in positive
    assert 'secondary@example.invalid' not in positive
    assert '990000000' not in positive
    assert all(t['source_kind'] != 'published_contact' for t in facts['linked_targets'])


@pytest.mark.parametrize('published_amount', [0, 750000])
def test_all_original_pocs_and_material_facts_survive_into_native_market_map(tmp_path, published_amount):
    def source(sweep):
        row = sweep['results']['sam.gov'][0]
        row.update(sub_agency='Source component', ceiling_dollars=published_amount,
            small_business_poc='Published Small Business Office',
            contacts=[dict(name='Source Primary', email='primary@example.gov', contact_type='primary')],
            poc_email='primary@example.gov', poc_title='Source Contract Specialist')
        row['raw_payload']['pointOfContact'] = [
            dict(fullName='Source Primary', email='primary@example.gov', phone='202-555-0101', type='primary'),
            dict(fullName='Source Secondary', email='secondary@example.gov', title='Program Analyst', type='secondary'),
            dict(fullName='Source Third', phone='202-555-0103', type='secondary'),
        ]
    context, graph = graph_fixture(tmp_path, source)
    assert graph['qualified_opportunities'] == ['N1']
    facts = graph['qualified_opportunity_records'][0]
    assert facts['published_value'] == published_amount
    assert facts['sub_agency'] == 'Source component'
    contacts = [t for t in facts['linked_targets'] if t['source_kind'] == 'published_contact']
    assert len(contacts) == 4
    indexed = {c['name']: c for c in contacts}
    assert indexed['Source Primary']['title'] == 'Source Contract Specialist'
    assert indexed['Source Primary']['phone'] == '202-555-0101'
    assert indexed['Source Secondary']['title'] == 'Program Analyst'
    assert indexed['Source Third']['title'] is None
    assert indexed['Source Third']['phone'] == '202-555-0103'
    mm = [asdict(r) for r in attach_v2_targets((), graph, notice_context=context)]
    assert len(mm) == 1
    assert mm[0]['value']['source_literal'] == str(published_amount)
    serialized = json.dumps(mm, default=str)
    for token in ('primary@example.gov', 'secondary@example.gov', '202-555-0103',
                  'Published Small Business Office'):
        assert token in serialized


def test_warm_reference_change_invalidates_native_products(tmp_path, monkeypatch):
    # Use an owned reference root; never mutate the operating reference file.
    reference_root = tmp_path / 'reference-root'
    monkeypatch.setattr(ledger, '_ROOT', reference_root)
    path = reference_root / 'data/reference/vehicles.json'
    write(path, {'vehicles': []})
    context, graph = graph_fixture(tmp_path / 'client')
    assert graph['qualified_opportunities'] == ['N1'] and context.unchanged()
    write(path, {'vehicles': [{'name': 'Changed reference'}]})
    assert not context.unchanged()
    assert attach_v2_targets((), graph, notice_context=context) == ()
