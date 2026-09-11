"""Step2 prespecified software acceptance. Synthetic, not a recall holdout."""
from __future__ import annotations

import copy
import json

import pytest

from agents.decisions.triage import deterministic_prefilter
from tools.relevance.engine import record_text_fields, score_record
from tools.relevance.scope import EngagementScope
from tools.relevance.taxonomy import (
    CapabilityTaxonomy, KillRule, load_taxonomy, retrieval_vocabulary,
    vocabulary_receipt,
)


@pytest.fixture
def tax():
    return load_taxonomy("apexanalytix")


def notice(text, **extra):
    return {"source_id": "fixture", "title": "Platform requirement",
            "description": text, "naics_code": "541512", **extra}


@pytest.mark.parametrize("text", [
    "The agency requires supplier master data validation.",
    "The requirement is vendor master validation before payment.",
    "The agency requires accounts payable recovery auditing.",
    "The agency requires recovery audit services for supplier payments.",
    "Provide bank account ownership validation.",
    "Provide vendor risk management.",
])
def test_supported_buyer_equivalents_reach_review_with_exact_proof(tax, text):
    row = notice(text)
    candidates, ruled, receipt = deterministic_prefilter([row], tax)
    assert candidates == [row] and not ruled
    evidence = receipt["screening_records"]["fixture"]
    assert evidence["screen_state"] == "SUPPORTED_CAPABILITY_AWAITING_JUDGMENT"
    assert evidence["qualification"] == "NOT_ESTABLISHED_BY_CAPABILITY_SCREEN"
    fields = dict(record_text_fields(row))
    for span in evidence["relevance"]["spans"]:
        source = fields[span["field"]]
        assert source[span["start"]:span["start"] + len(span["matched_text"])] == span["matched_text"]
        assert span["context"] in source
        assert span["evidence_ids"]


@pytest.mark.parametrize("text", [
    "The agency requires recovery audit services.",
    "The requirement is patient insurance eligibility validation using a data validation and enrichment API.",
    "Buy six projectors. Vendors must register in SAM.gov.",
    "Provide a data validation and enrichment API for weather records.",
    "Provide sanctions and PEP screening.",
    "Provide cafeteria staffing.",
    "Provide recovery audit services for patient insurance payments.",
    "Supplier payments are elsewhere. Provide recovery audit services for insurance claims.",
    "Provide supplier master. Data validation follows in a separate requirement.",
])
def test_retrieval_and_naics_do_not_manufacture_fit(tax, text):
    candidates, ruled, receipt = deterministic_prefilter([notice(text)], tax)
    assert not candidates and ruled["fixture"]["verdict"] == "discard"
    assert receipt["screening_records"]["fixture"]["relevance"]["score"] == 0


def test_aliases_share_one_scoring_vote_and_bounded_context_cannot_bridge_fields(tax):
    verdict = score_record(notice("Provide AP recovery audit and accounts payable recovery audit."), tax)
    assert verdict.score == 3 and verdict.core_terms == ["AP recovery audit"]
    row = notice("data validation", title="supplier master")
    assert score_record(row, tax).score == 0
    row = notice("", raw_payload={"screen_evidence_matches": [
        {"context": "recovery audit services"}, {"context": "supplier payments"}]})
    assert score_record(row, tax).score == 0


def test_context_guard_quote_is_retained_even_beyond_legacy_sixty_chars(tax):
    text = "Recovery audit services " + "specified " * 8 + "for supplier payments."
    verdict = score_record(notice(text), tax)
    assert verdict.core_terms == ["AP recovery audit"]
    assert "supplier payments" in verdict.spans[0].guard_context
    assert "supplier payments" not in verdict.spans[0].context


@pytest.mark.parametrize("kind", ["Award Notice", "award", "a"])
def test_awards_remain_historical_even_with_supported_capability_and_future_deadline(tax, kind):
    row = notice("Supplier master data validation", type=kind, response_deadline="2099-01-01")
    candidates, ruled, receipt = deterministic_prefilter([row], tax)
    assert not candidates
    assert ruled["fixture"]["screen_state"] == "HISTORICAL_MARKET_EVIDENCE"
    assert receipt["screening_records"]["fixture"]["relevance"]["score"] == 3


def test_missing_text_failed_inventory_and_verified_empty_are_distinct(tax):
    row = notice("")
    for attachment, expected, status in [
        ({}, "TEXT_INCOMPLETE", "NOT_RECORDED_UNKNOWN"),
        ({"status": "INVENTORY_UNAVAILABLE", "errors": ["HTTP failure"]},
         "ATTACHMENT_UNAVAILABLE", "INVENTORY_UNAVAILABLE"),
        ({"status": "VERIFIED_EMPTY_INVENTORY"}, "TEXT_INCOMPLETE", "VERIFIED_EMPTY_INVENTORY"),
    ]:
        candidates, ruled, receipt = deterministic_prefilter([row], tax, attachment_receipts={"fixture": attachment})
        assert not candidates and ruled["fixture"]["screen_state"] == expected
        assert ruled["fixture"]["verdict"] == "unscreened"
        assert receipt["screening_records"]["fixture"]["attachment_status"] == status
    row["description"] = "Provide vendor master validation."
    candidates, _, receipt = deterministic_prefilter([row], tax, attachment_receipts={
        "fixture": {"status": "FAILED", "errors": ["HTTP failure"]}})
    assert candidates == [row]
    assert receipt["screening_records"]["fixture"]["attachment_status"] == "FAILED"


def test_exclusions_and_scope_still_dominate_aliases(tax):
    row = notice("APEX Gamma Service provides supplier master data validation.")
    assert not deterministic_prefilter([row], tax)[0]
    row = notice("Provide vendor master validation.", agency="Department of Defense")
    assert not deterministic_prefilter([row], tax, engagement_scope=EngagementScope(
        preset="civilian"))[0]
    tax.exclude.append(KillRule(term="patient claims only", scope="record",
                               category="functional", reason="Explicit patient-only service."))
    row = notice("Patient claims only; supplier master data validation is incidental.",
                 raw_payload={"attachment_lookup": {"status": "FAILED"}})
    _, ruled, receipt = deterministic_prefilter([row], tax)
    assert ruled["fixture"]["screen_state"] == "EXPLICIT_FUNCTIONAL_MISMATCH"
    assert receipt["screening_records"]["fixture"]["attachment_status"] == "FAILED"


def test_reviewed_definition_maps_all_fourteen_and_drives_retrieval(tax):
    assert len(tax.retrieval_mappings) == 14
    terms = retrieval_vocabulary(tax)
    assert all(m.term in terms for m in tax.retrieval_mappings)
    assert "supplier master data validation" in terms
    receipt = vocabulary_receipt(tax, [*terms, "user added keyword"])
    assert len(receipt["definition_sha256"]) == 64
    assert receipt["wire_terms"][-1]["disposition"] == "unmapped_operator_term"
    changed = tax.model_copy(deep=True)
    changed.core[0].aliases[0].phrase = "changed buyer phrase"
    assert vocabulary_receipt(changed, [])['definition_sha256'] != receipt['definition_sha256']


@pytest.mark.parametrize("mutation", ["unknown_evidence", "missing_evidence", "unknown_capability", "duplicate_mapping", "blank_alias"])
def test_invalid_definition_cannot_silently_enable_screening(tax, mutation):
    payload = tax.model_dump()
    if mutation == "unknown_evidence": payload["core"][0]["evidence_ids"] = ["invented"]
    if mutation == "missing_evidence": payload["core"][0]["evidence_ids"] = []
    if mutation == "unknown_capability": payload["retrieval_mappings"][0]["capability"] = "invented"
    if mutation == "duplicate_mapping": payload["retrieval_mappings"].append(payload["retrieval_mappings"][0])
    if mutation == "blank_alias": payload["core"][0]["aliases"] = [{"phrase": " "}]
    with pytest.raises(ValueError): CapabilityTaxonomy(**payload)


def test_screen_receipt_round_trips_and_preserves_source_bytes(tax):
    row = notice("Provide vendor master validation.")
    original = copy.deepcopy(row)
    _, _, receipt = deterministic_prefilter([row], tax)
    assert row == original
    assert json.loads(json.dumps(receipt)) == receipt
    changed = notice("Provide vendor master validation. Changed source.")
    new = deterministic_prefilter([changed], tax)[2]
    assert new['screening_records']['fixture']['record_sha256'] != receipt['screening_records']['fixture']['record_sha256']


def test_unresolved_text_cannot_report_complete_decision_coverage(tax):
    from run_searches import _decision_coverage_receipt
    row = notice("")
    _, ruled, receipt = deterministic_prefilter([row], tax)
    coverage = _decision_coverage_receipt([row], ruled, prefilter_receipt=receipt)
    assert coverage['complete'] is False
    assert coverage['invalid_or_unscreened_count'] == 1


@pytest.mark.parametrize('resources, expected', [
    ({'resources_checked': False, 'attachments': None, 'errors': ['inventory lookup failed']}, 'INVENTORY_UNAVAILABLE'),
    ({'resources_checked': True, 'attachments': []}, 'VERIFIED_EMPTY_INVENTORY'),
    ({'stale_cache': True, 'attachments': []}, 'STALE_INVENTORY'),
    ({'attachments': []}, 'INVENTORY_UNAVAILABLE'),
    ({'resources_checked': None, 'attachments': [{'name': 'SOW.txt', 'resource_id': 'e' * 32}]}, 'INVENTORY_UNAVAILABLE'),
])
def test_real_attachment_producer_preserves_failed_vs_empty_receipt(monkeypatch, tax, resources, expected):
    import run_searches as rs
    from agents.schemas import RawOpportunity
    from tools.api import sam_notice_detail, sam_quota
    candidate = RawOpportunity(source='sam.gov', source_id='d' * 32,
                               title='Platform requirement', raw_payload={'type': 'Solicitation'})
    class Source:
        last_attachment_census = {}
        def attachment_candidates(self, *args, **kwargs): return [candidate]
    monkeypatch.setattr(sam_quota, 'calls_today', lambda: 0)
    monkeypatch.setattr(sam_notice_detail, 'fetch_notice_resources', lambda *a, **k: resources)
    from tools.api import sam_attachment_text
    def forbidden_extract(*a, **k):
        raise AssertionError('Unverified or empty inventory reached extraction')
    monkeypatch.setattr(sam_attachment_text, 'extract_public_attachment_text', forbidden_extract)
    rows, stats = rs._enrich_sam_public_attachments(
        [json.loads(candidate.model_dump_json())], Source(), object(), tax, None, limit=1)
    assert stats['attachment_record_receipts'][candidate.source_id]['status'] == expected
    _, ruled, receipt = deterministic_prefilter(rows, tax, attachment_receipts=stats['attachment_record_receipts'])
    assert receipt['screening_records'][candidate.source_id]['attachment_status'] == expected
    assert ruled[candidate.source_id]['verdict'] == 'unscreened'


def test_native_sam_query_and_saved_receipt_share_the_definition(tmp_path, monkeypatch, tax):
    import concurrent.futures
    import sys
    import run_searches as rs
    import tools.query_terms as qt
    import tools.relevance.taxonomy as taxonomies
    from tools.api import sam_extract
    from tests import test_workstation_native_run as fixture
    review_dir, *_ = fixture._seed_native_coexistence(tmp_path, monkeypatch)
    monkeypatch.setenv('LILA_EXPECT_CLIENT_NAME', fixture.CLIENT)
    monkeypatch.setenv('LILA_EXPECT_WORKSTATION_ID', fixture.WORKSTATION_ID)
    fixture._bind_native_owner(monkeypatch, review_dir)
    captured = {}
    fixture._patch_offline_success(monkeypatch, captured)
    monkeypatch.setattr(taxonomies, 'load_taxonomy', lambda _: tax)
    monkeypatch.setattr(qt, 'term_yield', lambda terms: [{'term': t, 'count': 0, 'sample_titles': []} for t in terms])
    monkeypatch.setattr(qt, 'append_term_yield_log', lambda *a, **k: None)
    queries = []
    class Source:
        last_census = {'complete': True, 'matched': 0}
        last_attachment_census = {}
        def search(self, query):
            queries.append(query)
            return []
        def attachment_candidates(self, *a, **k): return []
    class Executor(fixture._InertExecutor):
        def submit(self, runner, name, fn):
            if name == 'sam.gov': return fixture._InertFuture(runner(name, fn))
            return super().submit(runner, name, fn)
    monkeypatch.setattr(concurrent.futures, 'ThreadPoolExecutor', Executor)
    monkeypatch.setattr(sam_extract, 'SamExtractSource', Source)
    monkeypatch.setattr(sys, 'argv', ['run_searches.py', '--client', fixture.CLIENT, '--skip', 'triage', 'picture'])
    assert rs.main() == 0
    result = captured['payload']['results']
    assert 'supplier master data validation' in queries[0].keywords
    assert {m.term for m in tax.retrieval_mappings} <= set(queries[0].keywords)
    assert [r['term'] for r in result['capability_vocabulary']['wire_terms']] == queries[0].keywords
    assert result['term_yield_population']['comparable_to_final_sam_count'] is False
    assert all(set(r) == {'term', 'count', 'sample_titles'} for r in result['term_yield'])


def test_offline_audit_refuses_a_changed_source_or_baseline(tmp_path, tax):
    import hashlib
    from tools.relevance.audit_saved_screen import audit
    row = notice('Provide vendor master validation.')
    sweep = tmp_path / 'sweep.json'
    sweep.write_text(json.dumps({'results': {'sam.gov': [row]}}))
    baseline = tmp_path / 'baseline.jsonl'
    original = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    before = {'source_id': row['source_id'], 'title': row['title'], 'original_record_sha256': original,
              'saved_verdict': {'verdict': 'discard'}, 'recorded_retrieval_matches': ['naics']}
    baseline.write_text(json.dumps(before) + '\n')
    digest = hashlib.sha256(sweep.read_bytes()).hexdigest()
    records, summary = audit(sweep, baseline, 'apexanalytix', digest)
    assert summary['model_candidates'] == 1 and records[0]['stage_changed']
    with pytest.raises(ValueError, match='digest'): audit(sweep, baseline, 'apexanalytix', '0' * 64)
    before['original_record_sha256'] = '0' * 64
    baseline.write_text(json.dumps(before) + '\n')
    with pytest.raises(ValueError, match='digest'): audit(sweep, baseline, 'apexanalytix', digest)



def test_cleansing_is_exploratory_until_functional_equivalence_is_evidenced(tax):
    assert score_record(notice('Provide supplier data cleansing government services.'), tax).score == 0
    mapping = next(m for m in tax.retrieval_mappings if m.term == 'supplier data cleansing government')
    assert mapping.disposition == 'exploratory'


def test_nonmatching_attachment_is_auditable_without_qualification(monkeypatch, tax):
    import hashlib
    import run_searches as rs
    from agents.schemas import RawOpportunity
    from tools.api import sam_notice_detail, sam_quota, sam_attachment_text
    candidate = RawOpportunity(source='sam.gov', source_id='e' * 32, title='Platform support',
                               raw_payload={'type': 'Solicitation'})
    original = json.loads(candidate.model_dump_json())
    class Source:
        last_attachment_census = {}
        def attachment_candidates(self, *a, **k): return [candidate]
    monkeypatch.setattr(sam_quota, 'calls_today', lambda: 0)
    monkeypatch.setattr(sam_notice_detail, 'fetch_notice_resources', lambda *a, **k: {
        'resources_checked': True, 'attachment_inventory_hash': 'c' * 64,
        'attachments': [{'name': 'SOW.txt', 'resource_id': 'd' * 32}]})
    text = 'Unrelated cafeteria staffing. ' * 100
    monkeypatch.setattr(sam_attachment_text, 'extract_public_attachment_text', lambda *a, **k: {
        'name': 'SOW.txt', 'resource_id': 'd' * 32, 'sha256': 'f' * 64,
        'retrieved_at': '2026-09-11T00:00:00+00:00', 'source_url': 'https://sam.gov/', 'text': text})
    rows, stats = rs._enrich_sam_public_attachments([original], Source(), object(), tax, None, limit=1)
    assert rows == [original]
    _, ruled, receipt = deterministic_prefilter(rows, tax, attachment_receipts=stats['attachment_record_receipts'])
    evidence = receipt['screening_records'][candidate.source_id]['attachment_discovery_evidence']
    assert evidence['text_excerpt'] == text[:2000] and evidence['excerpt_truncated']
    assert evidence['text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert evidence['files'][0]['sha256'] == 'f' * 64
    assert evidence['screen_score'] == 0 and evidence['retained_in_candidate'] is False
    assert evidence['disposition'] == 'DISCOVERY_ONLY_NOT_REQUIREMENT_APPROVAL'
    assert ruled[candidate.source_id]['verdict'] == 'unscreened'
