"""Offline regressions; real Command Center release proof is recorded separately."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.assess.reviewed_cases import (
    ReviewedCases,
    load_cases,
    save_cases,
    supplement_live,
)
from agents.intake.dossier import CompanyDossier
from agents.intake.evidence_quality import review_claim_evidence
from agents.leadgen.press import PressLeadGenError, run_press
from agents.leadgen.press_html import render_html
from agents.leadgen.quality_baseline import check_reviewed_quality
from tests.test_leadgen_from_assess import _run as _base_run


def _run(*, live=None):
    from agents.assess.contracts import AssessRun
    data = _base_run(live=[] if live is not None else None).model_dump(mode="json")
    data["as_of"] = "2026-09-08T20:00:00Z"
    if live is not None:
        data["live"]["records"] = [r.model_dump(mode="json") for r in live]
    for key in ("live", "horizon", "partners"):
        data[key]["as_of"] = data["as_of"]
    return AssessRun.model_validate(data)

ROOT = Path(__file__).resolve().parents[1]


def _book():
    data = json.loads((ROOT / 'tests/fixtures/operating_research.json').read_text())
    data['client_name'] = 'Testco'
    return ReviewedCases.model_validate(data)


def _receipt():
    book = _book()
    run = _run(live=[c.record for c in book.cases])
    # Run time is advanced explicitly; source retrieval times remain unchanged.
    run = run.model_copy(update={'as_of': datetime(2026, 9, 8, 20, tzinfo=timezone.utc)})
    return run_press(assess=run, reviewed_cases=book), book


def test_reviewed_cases_keep_official_and_enriched_target_actions():
    receipt, book = _receipt()
    assert len(receipt.parents) == 3
    assert len(receipt.leads) == 3
    assert sum(len(l.targets) for l in receipt.leads) == 11
    assert {l.lead_tier.value for l in receipt.leads} == {'HOLD'}
    assert all(l.next_action.object == l.research.next_ask for l in receipt.leads)
    assert all(l.next_action.blocked_by for l in receipt.leads)
    html = render_html(receipt)
    for case in book.cases:
        for target in case.targets:
            assert target.name in html
            assert target.email in html
            assert target.next_ask.replace('&', '&amp;') in html
            assert str(target.source_url).replace('&', '&amp;') in html
    officials = [p for l in receipt.leads for p in l.external_pathway.published_contacts]
    assert len(officials) == 6
    assert all(p.email for p in officials)
    assert 'Faith Sakkos' not in html
    assert check_reviewed_quality(receipt, book)['passed']


def test_baseline_catches_target_loss_and_false_promotion():
    receipt, book = _receipt()
    bad = receipt.leads[0].model_copy(update={'targets': ()})
    with pytest.raises(ValueError, match='lost reviewed targets'):
        check_reviewed_quality(receipt.model_copy(update={'leads': (bad, *receipt.leads[1:])}), book)
    from agents.leadgen.enums import LeadTier
    bad = receipt.leads[0].model_copy(update={'lead_tier': LeadTier.LEAD_T1})
    with pytest.raises(ValueError, match='falsely promoted'):
        check_reviewed_quality(receipt.model_copy(update={'leads': (bad, *receipt.leads[1:])}), book)


def test_narrowing_preserves_rejected_case_history(tmp_path):
    book = _book()
    save_cases(book, tmp_path)
    original = book.cases[0]
    revised = original.model_copy(update={'research': original.research.model_copy(update={
        'status': 'rejected', 'priority': False, 'rationale': 'Technical demonstration did not meet the requirement.'})})
    changed = book.model_copy(update={'cases': (revised, *book.cases[1:])})
    save_cases(changed, tmp_path)
    assert len(list((tmp_path/'research_history/testco').glob('*.json'))) == 2
    current = load_cases('Testco', tmp_path)
    assert len(current.cases) == 3
    assert current.cases[0].research.rationale.startswith('Technical demonstration')
    assert current.cases[0].targets == original.targets
    receipt = run_press(assess=_run(live=[c.record for c in current.cases]), reviewed_cases=current)
    assert len(receipt.parents) == 3
    assert len([l for l in receipt.leads if l.research.priority]) == 2


def test_supplement_cannot_overwrite_discovery_or_expand_scope():
    book = _book()
    run = _run(live=[book.cases[0].record])
    live = supplement_live(run.live, book, 'all')
    assert len(live.records) == 3
    assert live.records[0] == run.live.records[0]
    with pytest.raises(ValueError, match='scope'):
        supplement_live(run.live, book, 'agency:dhs')


def test_wrong_client_and_malformed_dossier_fail(tmp_path):
    dossier = tmp_path/'dossier.json'
    dossier.write_text(json.dumps({'client_name':'Otherco','identity':{'query_name':'Otherco','status':'bound','official_domain':'example.com'}}))
    with pytest.raises(PressLeadGenError, match='different client'):
        run_press(assess=_run(), dossier_path=dossier)
    dossier.write_text('{"identity":{"status":"bound"}}')
    with pytest.raises(PressLeadGenError, match='invalid company dossier'):
        run_press(assess=_run(), dossier_path=dossier)


def test_extraction_rejects_titles_and_incident_product_mentions_without_erasing_evidence():
    dossier = CompanyDossier(client_name='Testco',identity={'query_name':'Testco','status':'bound','official_domain':'example.com'},
        evidence=[{'evidence_id':'e1','source_kind':'website','excerpt':'Case Study Ransomware Campaign: anonymous manufacturer.'},
                  {'evidence_id':'e2','source_kind':'website','excerpt':'Microsoft Office add-ins were used in an incident.'},
                  {'evidence_id':'e3','source_kind':'website','excerpt':'Customer Success Story: Hardis Group.'}],
        customers=[{'text':'Ransomware Campaign','evidence_ids':['e1']},{'text':'Microsoft','evidence_ids':['e2']},{'text':'Hardis Group','evidence_ids':['e3']}])
    result=review_claim_evidence(dossier)
    assert [c.text for c in result.customers] == ['Hardis Group']
    assert len(result.kept_out) == 2 and len(result.evidence) == 3
    assert all(c.rationale for c in result.kept_out)


def test_real_arista_dossier_rebinds_rivals_and_preserves_supported_products():
    path = ROOT/'data/review/arista_networks.dossier.json'
    if not path.exists():
        pytest.skip('real Arista operating input is not versioned; run local acceptance separately')
    dossier=CompanyDossier.model_validate_json(path.read_bytes())
    assert {'Morgan Stanley','Barclays','Citigroup','Activ Financial','Hardis Group'} <= {c.text for c in dossier.customers}
    assert not {'Microsoft','Spear Phishing Campaign','Malicious Browser Extensions','Ransomware Attack Unfolds'} & {c.text for c in dossier.customers}
    assert any('EOS' in c.text for c in dossier.offerings)
    rivals=[c for c in dossier.competitors if c.text.startswith(('Cisco','Juniper'))]
    assert len(rivals)==2 and all('SEC-2025-COMPETITION' in c.evidence_ids for c in rivals)


def test_ordinary_target_projection_retains_complete_action_without_casebook():
    from tests.test_leadgen_from_assess import _t1_target_actions
    run = _run()
    target = _book().cases[0].targets[0]
    projection = _t1_target_actions()
    projection['rows'][0]['target'] = target.model_dump(mode='json')
    receipt = run_press(assess=run, target_actions=projection)
    assert any(target in lead.targets for lead in receipt.leads)
    assert target.name in render_html(receipt)
