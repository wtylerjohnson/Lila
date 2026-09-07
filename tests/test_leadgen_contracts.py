"""Lead-gen WS0 contracts: parent/child grain, four-factor product, T1 split.

Doctrine 2026-09-06: OpportunityAssessment is parent; LeadRow is child;
targeting motion rule_id T1 is not LeadTier. Assess models stay untouched.
"""

from __future__ import annotations

import ast
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.assess.contracts import (
    AssessRun,
    AssessScope,
    EvidenceKind,
    EvidenceRef,
    EvidenceTier,
    EvidenceUse,
    LifecycleStage,
    LiveClassification,
    LiveRecommendation,
    LiveSolicitation,
    PartnerDirection,
    ProjectedWindow,
    ScopeAgency,
    ScopeMode,
)
from agents.leadgen import (
    SCHEMA_VERSION,
    ActionableExternalPathway,
    AssessmentSubjectKind,
    BuyingMotion,
    CommercialMotionKind,
    CommunicationPermission,
    Contactability,
    CurrentNextAction,
    DecisionTrace,
    LeadReadiness,
    LeadRow,
    LeadTier,
    NextActionVerb,
    OpportunityAssessment,
    PathwayKind,
    PublishedContact,
    SellerPathKind,
    SellerTransactionPath,
    TargetingRuleId,
    compose_assessment_id,
    compose_lead_id,
    enum_registry_values,
)
from agents.leadgen.schema_export import (
    SCHEMA_DIR,
    _file_stem,
    json_schemas,
    render_schema,
    write_schemas,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
LEADGEN_DIR = ROOT / "agents" / "leadgen"


def _evidence(*, eid="E1", notice_id="N1",
              kind=EvidenceKind.NOTICE, tier=EvidenceTier.NOTICE,
              primary=True):
    return EvidenceRef(
        evidence_id=eid, tier=tier, kind=kind,
        source_name="official source",
        source_url=f"https://sam.gov/opp/{notice_id}/view",
        retrieved_at=NOW, excerpt="The contractor shall provide packet capture.",
        primary_source=primary,
        supports=[EvidenceUse.REQUIREMENT],
    )


def _scope():
    return AssessScope(
        mode=ScopeMode.AGENCY,
        agencies=[ScopeAgency(name="Department of Homeland Security",
                              abbr="DHS")],
    )


def _parent(**overrides):
    return OpportunityAssessment(**{
        "assessment_id": compose_assessment_id(
            "R1", "live_solicitation", "L1"),
        "assess_run_id": "R1",
        "client_name": "Testco",
        "profile_version": "P1",
        "scope": _scope(),
        "as_of": NOW,
        "subject_kind": AssessmentSubjectKind.LIVE_SOLICITATION,
        "subject_id": "L1",
        "title": "Network visibility",
        "agency": "DHS",
        "notice_id": "N1",
        "solicitation_number": "70-TEST-26",
        "lifecycle": LifecycleStage.LIVE_SOLICITATION,
        "live_classification": LiveClassification.BID_NOW,
        "live_recommendation": LiveRecommendation.PURSUE,
        "lead_ids": (),
        **overrides,
    })


def _motion(**overrides):
    return BuyingMotion(**{
        "motion_id": "M1",
        "parent_subject_id": "L1",
        "kind": CommercialMotionKind.LIVE_BID,
        "stage": LifecycleStage.LIVE_SOLICITATION,
        "buyer_agency": "DHS",
        "buyer_component": "CISA",
        "clock": date(2026, 10, 1),
        **overrides,
    })


def _pathway(**overrides):
    return ActionableExternalPathway(**{
        "pathway_id": "P1",
        "kind": PathwayKind.SAM_NOTICE,
        "source_url": "https://sam.gov/opp/N1/view",
        "evidence": [_evidence()],
        "notice_id": "N1",
        "contactability": Contactability.ORGANIZATION_ONLY,
        **overrides,
    })


def _seller(**overrides):
    return SellerTransactionPath(**{
        "path_id": "S1",
        "kind": SellerPathKind.CHANNEL_RESELLER,
        "holder": "Example Reseller",
        **overrides,
    })


def _next(**overrides):
    return CurrentNextAction(**{
        "verb": NextActionVerb.REVIEW_REQUIREMENT_SPAN,
        "object": "requirement excerpt on N1",
        "due": date(2026, 10, 1),
        "communication_permission": CommunicationPermission.NONE,
        **overrides,
    })


def _lead(**overrides):
    return LeadRow(**{
        "lead_id": compose_lead_id(
            "oa:R1:live_solicitation:L1", "M1", "P1", "S1"),
        "parent_assessment_id": "oa:R1:live_solicitation:L1",
        "assess_run_id": "R1",
        "buying_motion": _motion(),
        "external_pathway": _pathway(),
        "seller_path": _seller(),
        "next_action": _next(),
        "lead_tier": LeadTier.LEAD_T1,
        "readiness": LeadReadiness.ACTIONABLE,
        "contactability": Contactability.ORGANIZATION_ONLY,
        "communication_permission": CommunicationPermission.NONE,
        "decision_trace_id": "dt:R1:L1",
        **overrides,
    })


def test_parent_is_not_assess_run_and_allows_zero_children():
    parent = _parent()
    assert parent.lead_ids == ()
    assert "live" not in OpportunityAssessment.model_fields
    assert "horizon" not in OpportunityAssessment.model_fields
    assert "can_release" not in OpportunityAssessment.model_fields
    assert OpportunityAssessment is not AssessRun
    assert "lead_ids" not in AssessRun.model_fields
    assert "lead_tier" not in LiveSolicitation.model_fields


def test_live_parent_requires_notice_id():
    with pytest.raises(ValidationError, match="notice_id"):
        _parent(notice_id=None)


def test_leadrow_requires_four_factors_and_parent():
    row = _lead()
    assert row.buying_motion.kind is CommercialMotionKind.LIVE_BID
    assert row.external_pathway.kind is PathwayKind.SAM_NOTICE
    assert row.seller_path.kind is SellerPathKind.CHANNEL_RESELLER
    assert row.next_action.verb is NextActionVerb.REVIEW_REQUIREMENT_SPAN
    payload = row.model_dump(mode="json")
    payload.pop("buying_motion")
    with pytest.raises(ValidationError):
        LeadRow.model_validate(payload)
    with pytest.raises(ValidationError):
        _lead(parent_assessment_id="")


def test_round_trip_parent_lead_and_trace():
    parent = _parent(lead_ids=["lr:oa:R1:live_solicitation:L1:M1:P1:S1"])
    row = _lead()
    trace = DecisionTrace(
        trace_id="dt:R1:L1", assess_run_id="R1", as_of=NOW,
        parent_assessment_id=parent.assessment_id, lead_id=row.lead_id,
        steps=("assess", "motion", "pathway", "path", "action", "tier"),
        evidence_ids=("E1",),
    )
    for obj in (parent, row, trace, row.buying_motion, row.external_pathway,
                row.seller_path, row.next_action):
        dumped = obj.model_dump(mode="json")
        restored = type(obj).model_validate(dumped)
        assert restored == obj
        assert dumped["schema_version"] == SCHEMA_VERSION


def test_targeting_rule_t1_is_not_lead_tier():
    """Targeting motion rule_id T1 is a different type and a different value."""

    assert TargetingRuleId.T1 is not LeadTier.LEAD_T1
    assert TargetingRuleId.T1.value == "T1"
    assert LeadTier.LEAD_T1.value == "LEAD_T1"
    assert TargetingRuleId.T1.value != LeadTier.LEAD_T1.value
    assert not isinstance(TargetingRuleId.T1, LeadTier)
    assert TargetingRuleId.T1.__class__ is not LeadTier
    motion = _motion(targeting_rule_id=TargetingRuleId.T1,
                     kind=CommercialMotionKind.RENEWAL)
    row = _lead(buying_motion=motion, lead_tier=LeadTier.LEAD_T2,
                readiness=LeadReadiness.ACTIONABLE)
    dumped = row.model_dump(mode="json")
    assert dumped["lead_tier"] == "LEAD_T2"
    assert dumped["buying_motion"]["targeting_rule_id"] == "T1"
    with pytest.raises(ValidationError):
        _lead(lead_tier="T1")
    registry = enum_registry_values()
    assert "T1" in registry["targeting_rule_id"]
    assert "T1" not in registry["lead_tier"]
    assert "LEAD_T1" in registry["lead_tier"]
    assert "LEAD_T1" not in registry["targeting_rule_id"]


def test_seller_path_reuses_partner_direction_literals():
    partner_values = {item.value for item in PartnerDirection}
    path_values = {item.value for item in SellerPathKind}
    assert partner_values <= path_values
    assert SellerPathKind.PATH_UNKNOWN.value == "path_unknown"


def test_unknown_seller_path_cannot_be_lead_t1():
    with pytest.raises(ValidationError, match="unknown seller path"):
        _lead(seller_path=_seller(kind=SellerPathKind.PATH_UNKNOWN))


def test_forecast_cannot_prove_sam_notice_or_mint_lead_t1():
    forecast = _evidence(kind=EvidenceKind.AGENCY_FORECAST,
                         tier=EvidenceTier.PROGRAM, primary=True)
    with pytest.raises(ValidationError, match="forecast"):
        _pathway(evidence=[forecast])
    watch_path = ActionableExternalPathway(
        pathway_id="P-forecast",
        kind=PathwayKind.AGENCY_FORECAST,
        source_url="https://www.dhs.gov/forecast",
        evidence=[forecast],
        contactability=Contactability.ORGANIZATION_ONLY,
    )
    with pytest.raises(ValidationError, match="actionable external pathway"):
        _lead(external_pathway=watch_path,
              buying_motion=_motion(
                  kind=CommercialMotionKind.ADJACENCY,
                  stage=LifecycleStage.FUNDED_INTENT,
                  window=ProjectedWindow(label="FY27"),
                  clock=None,
              ))


def test_watch_row_may_use_forecast_pathway():
    forecast = _evidence(kind=EvidenceKind.AGENCY_FORECAST,
                         tier=EvidenceTier.PROGRAM, primary=True)
    pathway = ActionableExternalPathway(
        pathway_id="P-forecast",
        kind=PathwayKind.AGENCY_FORECAST,
        source_url="https://www.dhs.gov/forecast",
        evidence=[forecast],
        contactability=Contactability.ORGANIZATION_ONLY,
    )
    row = _lead(
        external_pathway=pathway,
        buying_motion=_motion(
            kind=CommercialMotionKind.ADJACENCY,
            stage=LifecycleStage.FUNDED_INTENT,
            window=ProjectedWindow(label="FY27"),
            clock=None,
        ),
        lead_tier=LeadTier.WATCH,
        readiness=LeadReadiness.WATCHING,
    )
    assert row.lead_tier is LeadTier.WATCH
    assert row.external_pathway.kind is PathwayKind.AGENCY_FORECAST


def test_readiness_must_match_lead_tier():
    with pytest.raises(ValidationError, match="readiness"):
        _lead(lead_tier=LeadTier.HOLD, readiness=LeadReadiness.ACTIONABLE)


def test_buying_motion_requires_buyer_and_clock_or_window():
    with pytest.raises(ValidationError, match="clock or projected window"):
        _motion(clock=None, window=None)
    with pytest.raises(ValidationError):
        _motion(buyer_agency="")


def test_published_poc_pathway_requires_a_published_contact():
    with pytest.raises(ValidationError, match="published contact"):
        ActionableExternalPathway(
            pathway_id="P-poc",
            kind=PathwayKind.PUBLISHED_POC,
            source_url="https://sam.gov/opp/N1/view",
            evidence=[_evidence()],
            notice_id="N1",
            published_contacts=(),
        )
    pathway = ActionableExternalPathway(
        pathway_id="P-poc",
        kind=PathwayKind.PUBLISHED_POC,
        source_url="https://sam.gov/opp/N1/view",
        evidence=[_evidence()],
        notice_id="N1",
        published_contacts=[PublishedContact(name="Jane Contracting")],
        contactability=Contactability.PUBLISHED_POC,
    )
    row = _lead(
        external_pathway=pathway,
        contactability=Contactability.PUBLISHED_POC,
        next_action=_next(
            verb=NextActionVerb.CONFIRM_PUBLISHED_POC,
            object="Jane Contracting on N1",
            communication_permission=CommunicationPermission.CONFIRM_PUBLISHED,
        ),
        communication_permission=CommunicationPermission.CONFIRM_PUBLISHED,
        lead_tier=LeadTier.LEAD_T2,
        readiness=LeadReadiness.ACTIONABLE,
    )
    assert row.contactability is Contactability.PUBLISHED_POC


def test_decision_trace_rejects_naive_clock():
    with pytest.raises(ValidationError, match="timezone-aware"):
        DecisionTrace(
            trace_id="dt:R1:L1", assess_run_id="R1",
            as_of=datetime(2026, 9, 6, 12, 0),  # noqa: DTZ001
        )


def test_json_schema_export_keeps_enum_namespaces_apart():
    schemas = json_schemas()
    lead_schema = schemas["LeadRow"]
    motion_schema = schemas["BuyingMotion"]
    registry = schemas["EnumRegistry"]
    lead_tier = _schema_enum(lead_schema, "lead_tier")
    targeting = _schema_enum(motion_schema, "targeting_rule_id")
    assert "LEAD_T1" in lead_tier
    assert "T1" not in lead_tier
    assert "T1" in targeting
    assert "LEAD_T1" not in targeting
    assert registry["properties"]["lead_tier"]["enum"] == list(
        enum_registry_values()["lead_tier"])
    for name, schema in schemas.items():
        path = SCHEMA_DIR / f"{_file_stem(name)}.schema.json"
        checked = json.loads(path.read_text(encoding="utf-8"))
        assert render_schema(checked) == render_schema(schema)
    written = write_schemas(SCHEMA_DIR)
    assert {path.name for path in written} == {
        f"{_file_stem(name)}.schema.json" for name in schemas
    }


def test_leadgen_package_does_not_import_targeting_or_apollo():
    forbidden = ("targeting_rules", "apollo_targets", "apollo")
    for path in LEADGEN_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            joined = " ".join(modules).casefold()
            for token in forbidden:
                assert token not in joined, f"{path} imports {token}"


def test_schema_version_is_stable():
    assert SCHEMA_VERSION == "leadgen.contracts.v1"
    assert _lead().schema_version == SCHEMA_VERSION
    assert _parent().schema_version == SCHEMA_VERSION


def _schema_enum(schema: dict, field: str) -> list[str]:
    props = schema.get("properties") or {}
    direct = props.get(field) or {}
    if "enum" in direct:
        return list(direct["enum"])
    ref = direct.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        key = ref.split("/")[-1]
        return list(schema["$defs"][key]["enum"])
    any_of = direct.get("anyOf") or []
    for item in any_of:
        if "enum" in item:
            return list(item["enum"])
        ref = item.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            key = ref.split("/")[-1]
            return list(schema["$defs"][key]["enum"])
    raise AssertionError(f"no enum for {field} in {schema}")

