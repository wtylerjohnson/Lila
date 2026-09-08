"""Additive contact/action contract; synthetic data only."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.leadgen.pathway import PublishedContact
from agents.leadgen.targets import LeadTarget


def _target():
    fixture=Path(__file__).parent/'fixtures/operating_research.json'
    return json.loads(fixture.read_text())['cases'][0]['targets'][0]


def test_complete_target_roundtrip_retains_contact_action_and_authority():
    target=LeadTarget.model_validate(_target())
    assert LeadTarget.model_validate_json(target.model_dump_json()) == target
    assert target.email and target.phone and target.evidence and target.authority_boundary
    contact=PublishedContact(name=target.name,title=target.role,source_url=target.source_url,
                             email=target.email,phone=target.phone)
    assert PublishedContact.model_validate_json(contact.model_dump_json()) == contact


@pytest.mark.parametrize('key',['name','role','route','reason_to_contact','next_ask','evidence','source_kind'])
def test_incomplete_target_does_not_pass_as_seller_ready(key):
    payload=_target();payload.pop(key)
    with pytest.raises(ValidationError):
        LeadTarget.model_validate(payload)
