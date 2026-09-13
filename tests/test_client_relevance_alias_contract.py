"""Explicit Step2 C1 contract extension; no Assess or release authority."""
import copy
import pytest
from agents.reports import composer
from tools.relevance.engine import score_record
from tools.relevance.taxonomy import load_taxonomy


def notice(text):
    return {"source_id": "fixture", "description": text, "naics_code": "541512"}


def test_alias_basis_survives_existing_report_press_contract():
    tax = load_taxonomy("apexanalytix")
    from agents.reports import composer
    text = 'Recovery audit services ' + 'specified ' * 8 + 'for supplier payments.'
    row = notice(text)
    verdict = score_record(row, tax)
    basis = composer._verdict_client_relevance_basis(
        verdict, kind='record-core-match', source_identity='fixture',
        public_context={'band': 'competitors', 'buyer': 'VA', 'client': 'apexanalytix'})
    support = basis['supports'][0]
    assert composer._client_relevance_support_is_stored(
        support, term=basis['term'], kind=basis['kind'], taxonomy=tax,
        sweep={'results': {'sam.gov': [row]}})
    changed = notice(text.replace('supplier payments', 'patient benefits'))
    assert not composer._client_relevance_support_is_stored(
        support, term=basis['term'], kind=basis['kind'], taxonomy=tax,
        sweep={'results': {'sam.gov': [changed]}})



@pytest.mark.parametrize("tamper", ["unmapped_phrase", "stale_taxonomy", "missing_subject"])
def test_alias_contract_refuses_unreviewed_or_unbound_evidence(tamper):
    tax = load_taxonomy("apexanalytix")
    row = notice("Provide recovery audit services for supplier payments.")
    basis = composer._verdict_client_relevance_basis(
        score_record(row, tax), kind="record-core-match", source_identity="fixture",
        public_context={"band": "competitors", "buyer": "VA", "client": "apexanalytix"})
    bad = copy.deepcopy(basis)
    if tamper == "unmapped_phrase":
        bad["supports"][0].update(matched_text="claims reconciliation", quote="Provide claims reconciliation for supplier payments.")
    elif tamper == "stale_taxonomy":
        bad["profile_version"] = tax.version - 1
    else:
        bad["supports"][0]["quote"] = "Provide recovery audit services for patient benefits."
    with pytest.raises(ValueError, match="bound evidence"):
        composer._validate_client_relevance_basis(bad)
