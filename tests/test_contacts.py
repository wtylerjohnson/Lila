"""Step-3 contact layer tests: SAM POC capture, plan trimming, Apollo handoff.

Offline — fake engine, fixture payloads. No keys, no network.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decisions.contacts import _trim_candidate, build_contact_plan  # noqa: E402
from agents.decisions.schemas import (  # noqa: E402
    ContactPlan,
    ContactSearchSpec,
    KnownPoc,
    OrgType,
)
from tools.api.sam_gov import map_notice  # noqa: E402
from tools.crm.apollo_handoff import ApolloHandoffFinder  # noqa: E402
from tools.crm.base import FINDERS  # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAM = json.load(open(os.path.join(_HERE, "data", "raw", "sample_sam_response.json")))


# ---- SAM POC capture -------------------------------------------------------- #
def test_sam_maps_point_of_contact():
    opp = map_notice(_SAM["opportunitiesData"][0])
    assert len(opp.contacts) == 1  # the all-null secondary POC is dropped
    c = opp.contacts[0]
    assert c.name == "Jane Doe"
    assert c.title == "Contract Specialist"
    assert c.email == "jane.doe@va.gov"
    assert c.contact_type == "primary"


def test_sam_missing_poc_is_empty_list():
    opp = map_notice(_SAM["opportunitiesData"][1])
    assert opp.contacts == []


# ---- candidate trimming for the Claude context ------------------------------ #
def test_trim_candidate_keeps_contacts_drops_payload():
    rec = {
        "opportunity": {
            "source_id": "abc", "source": "sam.gov", "title": "T", "agency": "VA",
            "naics_code": "541512", "contacts": [{"name": "Jane Doe"}],
            "raw_payload": {"huge": "blob"},
        },
        "verified": True,
        "market_evidence": {"summary": {"award_count": 3}},
        "fit_rationale": {"verdict": "strong_fit"},
    }
    t = _trim_candidate(rec)
    assert t["opportunity_id"] == "abc"
    assert t["contacts"] == [{"name": "Jane Doe"}]
    assert t["market_summary"] == {"award_count": 3}
    assert "raw_payload" not in json.dumps(t)


def test_build_contact_plan_uses_fake_engine_and_filters_unverified():
    captured = {}

    class FakeEngine:
        def deliberate(self, *, layer, system_prompt, context, schema):
            captured.update(context=context, layer=layer)
            return ContactPlan(client_name=context["client_name"], strategy_note="x")

    report = {
        "candidates": [
            {"opportunity": {"source_id": "keep"}, "verified": True},
            {"opportunity": {"source_id": "drop"}, "verified": False},
        ]
    }
    plan = build_contact_plan("Acme", report, engine=FakeEngine())
    assert plan.client_name == "Acme"
    assert captured["layer"] == "contacts"
    ids = [c["opportunity_id"] for c in captured["context"]["qualified_candidates"]]
    assert ids == ["keep"]


# ---- Apollo handoff file ----------------------------------------------------- #
def _plan() -> ContactPlan:
    return ContactPlan(
        client_name="Acme Federal",
        strategy_note="Lead with the VA notice POC; team with the incumbent.",
        known_pocs=[KnownPoc(opportunity_id="n1", name="Jane Doe", email="jane.doe@va.gov", org="VA")],
        searches=[
            ContactSearchSpec(
                org_name="Veterans Affairs TAC",
                org_type=OrgType.AGENCY_OFFICE,
                person_titles=["Contracting Officer", "OSDBU Director"],
                rationale="Issuing office for notice n1.",
                related_opportunity_ids=["n1"],
            ),
            ContactSearchSpec(
                org_name="BigPrime Inc",
                org_type=OrgType.INCUMBENT,
                domain="bigprime.com",
                person_titles=["VP Capture"],
                seniorities=["vp"],
                rationale="Top incumbent per USAspending.",
                related_opportunity_ids=["n1"],
            ),
        ],
        citations=["https://sam.gov/opp/n1"],
        generated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_handoff_writes_cowork_ready_spec(tmp_path):
    finder = ApolloHandoffFinder(out_dir=str(tmp_path))
    path = finder.deliver(_plan())
    doc = json.load(open(path))
    assert path.endswith("acme_federal.apollo.json")
    assert doc["kind"] == "apollo_handoff"
    assert doc["known_pocs"][0]["email"] == "jane.doe@va.gov"
    s0, s1 = doc["searches"]
    assert s0["person_titles"] == ["Contracting Officer", "OSDBU Director"]
    assert "q_organization_domains" not in s0  # no domain known
    assert s1["q_organization_domains"] == ["bigprime.com"]
    assert s1["person_seniorities"] == ["vp"]
    assert doc["instructions"]  # a Cowork session can act on the file alone


def test_apollo_handoff_registered():
    assert "apollo-handoff" in [f.name for f in FINDERS.all()]
