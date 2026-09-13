"""Read-only candidate review: all input/review/runtime writes are tmp_path only.

Reviewer metadata below is synthetic test data, never a completed analyst review.
"""
import json
import pytest
from pathlib import Path
from types import SimpleNamespace

from agents.assess.reviewed_cases import ReviewedCases, ReviewedSubject, load_cases
from agents.assess.ledger import build_assess_run, materialize_current_assess_run
from agents.leadgen.targets import LeadResearch, LeadTarget
from agents.assess.contracts import EvidenceRef, EvidenceKind, EvidenceTier
from agents.leadgen.export_assess import export_current_assess_run
from agents.leadgen.press import run_press
from agents.leadgen.press_html import render_html
from agents.leadgen.quality_baseline import check_reviewed_quality
from tests.test_research_subjects import research_sweep, NOW, BINDING


def make_book(monkeypatch):
    sweep, profile = research_sweep(monkeypatch)
    run, _, _ = build_assess_run('NETSCOUT', sweep, profile, BINDING, as_of=NOW)
    overlays = []
    for subject in run.research.items:
        if subject.source_kind != 'award':
            continue
        tech = '70B04C24F00000415' in subject.source_record_id
        name, role, organization, url, email = (
            ('Bill Lytle', 'SEWP Program Manager', 'TechAnax',
             'https://www.techanax.com/nasa-sewp-v', 'Bill.Lytle@TechAnax.com')
            if tech else
            ('SIRC', 'Organization contact; individual owner unknown', 'SIRC',
             'https://www.sirc.net/', 'info@sirc.net'))
        ask = ('Which SKUs and option decisions does TechAnax cover?' if tech
               else 'Which products are supplied under NETSCOUT or equal?')
        route = ('Partner program manager; account ownership unknown' if tech
                 else 'Organization-only route; named owner unknown')
        boundary = 'Company route; not government buyer or verified account owner'
        evidence = EvidenceRef(
            evidence_id='synthetic-route-'+organization.lower(),
            tier=EvidenceTier.DISCOVERY, kind=EvidenceKind.WEB_LEAD,
            source_name=organization+' published route (synthetic test excerpt)',
            source_url=url, excerpt=boundary)
        target = LeadTarget(
            name=name, role=role, organization=organization,
            source_kind='company_published', source_url=url, email=email,
            contact_status='Published company route; identity role bounded',
            route=route, reason_to_contact='Clarify purchasing route',
            next_ask=ask, authority_boundary=boundary, evidence=(evidence,))
        research = LeadResearch(
            status='investigation', priority=True,
            rationale='Synthetic review contract test',
            buyer_requirement=subject.evidence[0].excerpt,
            fit_hypothesis='Actual products remain unknown', route=route,
            why_now='Current recorded period; continuation unknown', next_ask=ask,
            open_questions=subject.open_questions,
            reviewed_by='SYNTHETIC TEST ONLY', reviewed_at=NOW,
            evidence=subject.evidence+(evidence,))
        overlays.append(ReviewedSubject(
            subject_id=subject.subject_id, source_sha256=subject.source_sha256,
            research=research, targets=(target,)))
    return sweep, profile, ReviewedCases(
        schema_version='reviewed_cases.v2', client_name='NETSCOUT',
        subjects=tuple(overlays))


def test_sanctioned_import_materialize_export_retains_reviewed_subjects(tmp_path, monkeypatch):
    import tools.reviewed_cases as cli
    import agents.assess.approval as approval
    import tools.capability as capability
    import agents.reports.document as document
    import tools.assess_refresh as refresh
    sweep, profile, book = make_book(monkeypatch)
    root = tmp_path/'runtime'
    review, state = root/'data/review', root/'data/state/assess_runs'
    root.mkdir()
    input_path = tmp_path/'reviewed-input.json'
    input_path.write_text(book.model_dump_json())
    sweep_path = tmp_path/'sweep.json'
    sweep_path.write_text(json.dumps(sweep))
    monkeypatch.setattr(cli, '__file__', str(root/'tools/reviewed_cases.py'))
    monkeypatch.setattr(capability, 'require_profile', lambda *a, **kw: profile)
    monkeypatch.setattr(approval, 'current_assess_binding', lambda *a, **kw: BINDING)
    monkeypatch.setattr(approval, '_assess_approval_for_materialization',
                        lambda *a, **kw: (None, 'not_requested', ()))
    monkeypatch.setattr(document, 'build_document', lambda *a, **kw: None)
    monkeypatch.setattr(cli, 'refresh_current_assess_run_if_active',
        lambda client, **kw: refresh.refresh_current_assess_run_if_active(
            client, sweep_path=sweep_path, state_dir=state, review_dir=review,
            designator='all'))
    assert cli.main(['--client', 'NETSCOUT', '--input', str(input_path)]) == 0
    assert load_cases('NETSCOUT', review) == book
    # First import intentionally preserves the absent-pointer operator boundary.
    assert not state.exists()
    run, _, _ = materialize_current_assess_run(
        'NETSCOUT', sweep_path=sweep_path, state_dir=state, review_dir=review)
    before = {s.subject_id: (s.source_sha256, s.evidence) for s in run.research.items}
    exported = export_current_assess_run('NETSCOUT', scope='all', state_dir=state)
    receipt = run_press(assess=exported, reviewed_cases=load_cases('NETSCOUT', review))
    assert check_reviewed_quality(receipt, book)['passed']
    html = render_html(receipt)
    for overlay in book.subjects:
        parent = next(p for p in receipt.parents if p.subject_id == overlay.subject_id)
        assert parent.research == overlay.research and parent.targets == overlay.targets
        assert not parent.lead_ids
        assert before[overlay.subject_id] == (parent.research_subject.source_sha256,
                                             parent.research_subject.evidence)
        target = overlay.targets[0]
        for text in (target.name, target.role, target.email, target.next_ask,
                     target.authority_boundary, str(target.source_url)):
            assert text in html
    # Refresh after sanctioned input load also stays on the ordinary materializer.
    assert cli.main(['--client', 'NETSCOUT', '--input', str(input_path)]) == 0


@pytest.mark.parametrize("loss", ["subject", "target", "ask", "retained_id", "deprioritized", "rejected"])
def test_release_retention_refuses_reviewed_source_loss(tmp_path, monkeypatch, loss):
    import agents.golden_press.product_bundle as owner
    sweep, profile, old_book = make_book(monkeypatch)
    old_run, _, _ = build_assess_run(
        'NETSCOUT', sweep, profile, BINDING, as_of=NOW, reviewed_cases=old_book)
    old_receipt = run_press(assess=old_run, reviewed_cases=old_book)
    baseline = check_reviewed_quality(old_receipt, old_book)
    previous = tmp_path/'prior_release'
    previous.mkdir()
    (previous/'evidence_pack.json').write_text('{"records":[]}')
    (previous/'quality_baseline.json').write_text(json.dumps(baseline))
    (previous/'leadgen_status.json').write_text('{"status":"complete"}')
    monkeypatch.setattr(owner, 'product_release_state', lambda *a, **kw: {
        'releasable': True, 'manifest_path': str(previous/'manifest.json')})
    raw=old_book.model_dump(mode='json')
    if loss=='subject':raw['subjects']=[]
    elif loss=='target':raw['subjects'][0]['targets']=[]
    elif loss=='ask':raw['subjects'][0]['research']['next_ask']=None
    if loss=='ask':
        with pytest.raises(ValueError):ReviewedCases.model_validate(raw)
        with pytest.raises(owner.ProductReleaseBlocked,match='detail removed'):
            owner._require_output_retention(tmp_path,'netscout',SimpleNamespace(records=[]), {
                'status':'complete','reviewed_cases':raw,'quality_baseline':baseline})
        return
    if loss in {'deprioritized','rejected'}:
        raw['subjects'][0]['research'].update(status=loss,priority=False)
    removed=ReviewedCases.model_validate(raw)
    new_run, _, _ = build_assess_run(
        'NETSCOUT', sweep, profile, BINDING, as_of=NOW, reviewed_cases=removed)
    new_receipt = run_press(assess=new_run, reviewed_cases=removed)
    assert check_reviewed_quality(new_receipt, removed)['passed']
    quality=check_reviewed_quality(new_receipt,removed)
    if loss=='retained_id':quality['retained_research_subject_ids']=[]
    if loss in {'deprioritized','rejected'}:
        owner._require_output_retention(tmp_path,'netscout',SimpleNamespace(records=[]), {
            'status':'complete','reviewed_cases':raw,'quality_baseline':quality})
        assert all(not p.lead_ids for p in new_receipt.parents if p.research_subject)
        return
    with pytest.raises(owner.ProductReleaseBlocked,match='history removed|detail removed|target removed'):
        owner._require_output_retention(tmp_path, 'netscout', SimpleNamespace(records=[]), {
            'status': 'complete', 'reviewed_cases': removed.model_dump(mode='json'),
            'quality_baseline': quality})
    # Identical second release retains history, source identity, contacts and asks.
    owner._require_output_retention(tmp_path,'netscout',SimpleNamespace(records=[]), {
        'status':'complete','reviewed_cases':old_book.model_dump(mode='json'),
        'quality_baseline':baseline})


