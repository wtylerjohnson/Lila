"""Golden reference inventory for the Candidate Review v1 product contract.

The three hand-finished reports are migration inputs, not renderer bytes.  The
fixtures freeze their good content and visual/interaction signals while the
playbook overrides known defects (leadership cards, incomplete editing, and
legacy workflow controls).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CandidateKind,
    CandidateMember,
    CandidateReviewDocument,
    CandidateUnit,
    ClusterLevel,
    DateStatus,
    DecisionBoundary,
    EditorState,
    EvidenceKind,
    NoticeRole,
    NoticeStatus,
    EvidenceRecord,
    EvidenceUse,
    KpiKind,
    KpiTile,
    LifecycleKind,
    MoneyBasis,
    MoneyValue,
    SourceIdentity,
    SourceTier,
    StrategicCorridorIdentity,
    TickerItem,
)
from agents.candidate_review_v1.event_research import (
    CLIENT_EVENT_QUERY_FAMILIES,
    CLIENT_EVENT_SOURCE_CLASSES,
)


_FIXTURES = Path(__file__).parent / "fixtures" / "candidate_review_v1"

_SECTIONS = [
    ("forecast", "360° assessment: what the pattern suggests"),
    ("candidate-review", "Candidate opportunities for review"),
    ("signals", "Vehicle, partner, and market signals"),
    ("accounts", "Past awards and competitive analysis"),
    ("capabilities", "Preliminary keywords and capability search concepts"),
    ("calendar", "Federal opportunity calendar"),
    ("method", "Assess | Target | Execute"),
    ("evidence", "Evidence dock"),
]

_DOCUMENT_ACTIONS = [
    "toggle-edit",
    "save-draft",
    "reset",
    "download-html",
    "print",
]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest() -> dict:
    return _read(_FIXTURES / "manifest.json")


def _reports() -> list[dict]:
    manifest = _manifest()
    return [
        _read(_FIXTURES / manifest["reports"][slug]["expected_contract"])
        for slug in ("mark43", "imerit", "riverbed")
    ]


def _section_pairs(payload: dict) -> list[tuple[str, str]]:
    return [
        (row["id"], row["heading"])
        for row in payload["exact_section_contract"]
    ]


def _golden_document(report: dict) -> CandidateReviewDocument:
    """Hydrate each compact content golden through the same strict schema."""

    client_id = report["client_id"]
    run_id = f"golden-{client_id}"
    scope_hash = "a" * 64
    now = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
    contract = report["candidate_contract"]
    is_notice_golden = contract["candidate_kind_counts"]["current_notice"] > 0

    evidence = []
    member_evidence: dict[str, str] = {}
    for candidate in contract["candidates_in_order"]:
        for record_id in candidate["member_record_ids"]:
            evidence_id = f"E-MEMBER-{len(member_evidence) + 1:02d}"
            member_evidence[record_id] = evidence_id
            notice = candidate["kind"] == "current_notice"
            source_system = "sam.gov" if notice else "usaspending.gov"
            source_url = (
                f"https://sam.gov/opp/{record_id}/view"
                if notice
                else f"https://www.usaspending.gov/award/{record_id}"
            )
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id,
                client_id=client_id,
                run_id=run_id,
                scope_sha256=scope_hash,
                source_identity=SourceIdentity(
                    source_system=source_system, record_id=record_id),
                source_tier=(SourceTier.NOTICE if notice else SourceTier.PROGRAM),
                source_kind=(EvidenceKind.NOTICE if notice else EvidenceKind.AWARD),
                source_name=("SAM.gov" if notice else "USAspending.gov"),
                source_url=source_url,
                retrieved_at=now - timedelta(hours=1),
                title=f"Golden member record {record_id}",
                excerpt="The official record supplies the member evidence retained by the golden card.",
                official_source=True,
                primary_source=True,
                supports=(EvidenceUse.REQUIREMENT, EvidenceUse.TIMING),
                notice_status=(NoticeStatus.ACTIVE if notice else None),
                notice_role=(
                    NoticeRole.END_USER_REQUIREMENT if notice else None
                ),
                verified_at=(now - timedelta(minutes=30) if notice else None),
            ))

    while len(evidence) < report["reference_inventory"]["evidence_records"]:
        ordinal = len(evidence) + 1
        record_id = f"{client_id.upper()}-SUPPORT-{ordinal:02d}"
        evidence.append(EvidenceRecord(
            evidence_id=f"E-SUPPORT-{ordinal:02d}",
            client_id=client_id,
            run_id=run_id,
            scope_sha256=scope_hash,
            source_identity=SourceIdentity(
                source_system="usaspending.gov", record_id=record_id),
            source_tier=SourceTier.MARKET,
            source_kind=EvidenceKind.AWARD,
            source_name="USAspending.gov",
            source_url=f"https://www.usaspending.gov/award/{record_id}",
            retrieved_at=now - timedelta(hours=1),
            title=f"Golden supporting record {ordinal}",
            excerpt="The official supporting record supplies retained market context.",
            official_source=True,
            primary_source=True,
            supports=(EvidenceUse.BUYER, EvidenceUse.FUNDING),
        ))

    candidates = []
    for candidate_index, row in enumerate(contract["candidates_in_order"]):
        kind = CandidateKind(row["kind"])
        lifecycle = (
            LifecycleKind.LIVE_SOLICITATION
            if kind == CandidateKind.CURRENT_NOTICE
            else LifecycleKind.RECOMPETE_RESEARCH
        )
        boundary = DecisionBoundary(
            buyer_key=(
                "golden:official-notice-buyer"
                if is_notice_golden
                else f"golden:analyst-lane:{row['stable_reference_key']}"
            ),
            deadline=None,
            eligibility_key="validate-from-source",
            lifecycle=lifecycle,
            program_key=row["stable_reference_key"],
            access_route_key=("sam-notice" if is_notice_golden else "research"),
            analyst_action_key="validate-evidence-and-next-event",
        )
        members = tuple(
            CandidateMember(
                source_identity=next(
                    item.source_identity for item in evidence
                    if item.evidence_id == member_evidence[record_id]
                ),
                member_evidence_ids=(member_evidence[record_id],),
                decision_boundary=boundary,
            )
            for record_id in row["member_record_ids"]
        )
        corridor = (
            StrategicCorridorIdentity(
                corridor_id=row["stable_reference_key"],
                decision_boundary=boundary,
            )
            if kind == CandidateKind.RESEARCH_CORRIDOR else None
        )
        candidate_money = ()
        if candidate_index == 0:
            source_evidence_id = evidence[len(member_evidence)].evidence_id
            candidate_money = (
                MoneyValue(
                    money_id="GOLDEN-OBLIGATION",
                    basis=MoneyBasis.OBLIGATED_TO_DATE,
                    amount=Decimal("1000000"), as_of=now.date(),
                    source_field="golden.obligated_to_date",
                    evidence_ids=(source_evidence_id,)),
                MoneyValue(
                    money_id="GOLDEN-RESEARCH-SCALE",
                    basis=MoneyBasis.AGGREGATE_HISTORY,
                    amount=Decimal("2000000"), as_of=now.date(),
                    source_field="golden.aggregate_history",
                    evidence_ids=(source_evidence_id,)),
            )
        candidates.append(CandidateUnit(
            candidate_id=row["stable_reference_key"],
            client_id=client_id,
            kind=kind,
            title=row["title"],
            agency="Golden reference agency",
            lifecycle=lifecycle,
            cluster_level=(
                ClusterLevel.EXACT_RECORD
                if kind == CandidateKind.CURRENT_NOTICE
                else ClusterLevel.STRATEGIC_CORRIDOR
            ),
            members=members,
            strategic_corridor=corridor,
            money=candidate_money,
            records_show="The retained official records show this review lane.",
            may_suggest="The pattern may indicate a client-relevant federal buying path.",
            validate_next="Validate requirements, buyer intent, timing, and access.",
            cluster_reason="The displayed records lead to one analyst validation action.",
            strategic_action="Review the evidence and test the next buying hypothesis.",
            distinctness_explanation="This lane remains distinct by action, program, and access route.",
            inference_chain="Official record to pattern signal to analyst validation.",
            falsifier="The authoritative package does not support the inferred client relevance.",
            watch_trigger="A new official notice, forecast, budget, or program record appears.",
        ))

    ticker_items = tuple(
        TickerItem(
            ticker_id=f"TICK-{index + 1:02d}",
            label=f"Golden account signal {index + 1}",
            source_evidence_id=evidence[index % len(evidence)].evidence_id,
            evidence_ids=(evidence[index % len(evidence)].evidence_id,),
            historical_context=True,
        )
        for index in range(report["reference_inventory"]["ticker_primary_items"])
    )
    kpi_kinds = (
        KpiKind.CANDIDATE_COUNT,
        KpiKind.SOURCE_COVERAGE,
        KpiKind.OBLIGATION,
        KpiKind.RESEARCH_SCALE,
    )
    kpis = tuple(
        KpiTile(
            kpi_id=f"KPI-{index + 1}", kind=kind,
            eyebrow="Assessment signal",
            value=(
                candidates[0].money[0].display_value
                if kind == KpiKind.OBLIGATION
                else candidates[0].money[1].display_value
                if kind == KpiKind.RESEARCH_SCALE
                else str(len(candidates))
            ),
            title=f"Golden KPI {index + 1}",
            note="Typed context derived from retained evidence.",
            evidence_ids=(
                candidates[0].money[0].evidence_ids
                if kind == KpiKind.OBLIGATION
                else candidates[0].money[1].evidence_ids
                if kind == KpiKind.RESEARCH_SCALE
                else (evidence[index].evidence_id,)
            ),
            computed=(kind == KpiKind.CANDIDATE_COUNT),
            money_id=(
                "GOLDEN-OBLIGATION" if kind == KpiKind.OBLIGATION
                else "GOLDEN-RESEARCH-SCALE"
                if kind == KpiKind.RESEARCH_SCALE else None
            ),
        )
        for index, kind in enumerate(kpi_kinds)
    )
    document_id = report["reference_artifact"]["report_id"]
    return CandidateReviewDocument(
        document_id=document_id,
        baseline_document_sha256="d" * 64,
        client_name=report["client_display_name"],
        binding=ArtifactBinding(
            client_id=client_id,
            client_name=report["client_display_name"],
            run_id=run_id,
            scope_designator="golden-reference",
            scope_sha256=scope_hash, profile_sha256="b" * 64,
            evidence_snapshot_sha256="c" * 64),
        as_of=now, generated_at=now - timedelta(minutes=1),
        evidence=tuple(evidence), candidates=tuple(candidates),
        ticker_items=ticker_items, kpi_tiles=kpis,
        editor_state=EditorState(
            client_id=client_id, document_id=document_id,
            baseline_document_sha256="d" * 64,
            candidate_order=tuple(item.candidate_id for item in candidates)),
    )


def test_all_goldens_freeze_the_same_eight_section_contract():
    manifest = _manifest()
    assert _section_pairs(manifest) == _SECTIONS
    assert [row["ordinal"] for row in manifest["exact_section_contract"]] \
        == list(range(1, 9))

    for report in _reports():
        assert _section_pairs(report) == _SECTIONS
        headings = "\n".join(heading for _section, heading in _SECTIONS)
        assert "Priority federal routes" not in headings
        assert "leadership" not in {section for section, _heading in _SECTIONS}


def test_reference_hashes_are_locked_and_match_when_artifacts_are_available():
    manifest = _manifest()
    expected = {
        "mark43": "f774609b0b7cfcc119d435b45355c21806fc466c2d7fb81d72362f8319fc5c33",
        "imerit": "6c5583ffc4396b719fea2d3557b3e27a2c8f2cb2c3d9718ae39996f92b50ffe9",
        "riverbed": "6af1535177ec7c48f56813813bc4b7dddcba09e7627fa593650b68fdd7c565ff",
    }
    for slug, digest in expected.items():
        row = manifest["reports"][slug]
        assert row["sha256"] == digest
        source = Path(row["reference_path"])
        if source.exists():
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_reference_candidate_content_and_membership_are_preserved():
    reports = {report["client_id"]: report for report in _reports()}
    assert [
        candidate["title"]
        for candidate in reports["mark43"]["candidate_contract"]
        ["candidates_in_order"]
    ][0] == "Integrated Workforce and Readiness Platform (ARMOR)"
    assert [
        candidate["title"]
        for candidate in reports["imerit"]["candidate_contract"]
        ["candidates_in_order"]
    ][0] == "NEIC AI/ML and Data Streaming Development"

    riverbed = reports["riverbed"]["candidate_contract"]
    assert riverbed["display_count"] == 4
    assert riverbed["member_evidence_count"] == 8
    assert [row["member_count"] for row in riverbed["candidates_in_order"]] \
        == [2, 4, 1, 1]
    assert sum(
        len(row["member_record_ids"])
        for row in riverbed["candidates_in_order"]
    ) == riverbed["member_evidence_count"]
    assert len({
        member
        for row in riverbed["candidates_in_order"]
        for member in row["member_record_ids"]
    }) == riverbed["member_evidence_count"]


def test_golden_counts_are_client_facts_not_global_quotas():
    reports = {report["client_id"]: report for report in _reports()}
    assert reports["mark43"]["reference_inventory"]["candidate_cards"] == 8
    assert reports["imerit"]["reference_inventory"]["candidate_cards"] == 8
    assert reports["riverbed"]["reference_inventory"]["candidate_cards"] == 4
    for report in reports.values():
        inventory = report["reference_inventory"]
        assert inventory["evidence_records"] == 18
        assert inventory["ticker_primary_items"] == 11
        assert inventory["kpi_tiles"] == 4
        assert inventory["data_edit_ids"] == inventory["unique_data_edit_ids"]


def test_visible_control_inventory_is_exact_and_legacy_workflow_is_forbidden():
    manifest = _manifest()
    controls = manifest["required_controls"]
    assert [
        row["data_action"]
        for row in controls["persistent_document_controls"]
    ] == _DOCUMENT_ACTIONS
    assert controls["contextual_editor_controls"] == [{
        "data_action": "undo-soft-delete",
        "label": "Undo",
        "visibility": "after_soft_delete",
        "persistent": False,
    }]
    bottom = controls["persistent_bottom_workflow_controls"]
    assert bottom == [{
        "data_workflow_action": "refresh-analyst-layer",
        "label": "Refresh from Analyst Layer",
        "required_occurrences": 1,
    }]

    forbidden = manifest["forbidden_contract"]
    forbidden_copy = " ".join(forbidden["visible_control_labels_exact"]).lower()
    for phrase in ("pursuit dossier", "re-screen", "re-search", "re-run",
                   "re-scan", "crm", "pipeline"):
        assert phrase in forbidden_copy
    assert "#leadership" in forbidden["dom_selectors"]
    assert "Priority federal routes" in forbidden["visible_text_exact"]


def test_each_report_carries_the_same_required_and_forbidden_contract():
    manifest = _manifest()
    for report in _reports():
        assert report["required_controls"]["persistent_document_controls"] \
            == manifest["required_controls"]["persistent_document_controls"]
        assert report["required_controls"]["contextual_editor_controls"] \
            == manifest["required_controls"]["contextual_editor_controls"]
        bottom = report["required_controls"] \
            ["persistent_bottom_workflow_controls"]
        assert len(bottom) == 1
        assert bottom[0]["data_workflow_action"] == "refresh-analyst-layer"
        assert bottom[0]["required_occurrences"] == 1
        assert report["forbidden_text_exact"] \
            == manifest["forbidden_contract"]["visible_text_exact"]
        assert report["forbidden_dom_selectors"] \
            == manifest["forbidden_contract"]["dom_selectors"]


def test_all_three_content_goldens_validate_through_one_client_neutral_schema():
    documents = [_golden_document(report) for report in _reports()]
    assert [document.binding.client_id for document in documents] \
        == ["mark43", "imerit", "riverbed"]
    for report, document in zip(_reports(), documents):
        expected = report["candidate_contract"]
        assert len(document.candidates) == expected["display_count"]
        assert sum(len(item.members) for item in document.candidates) \
            == expected["member_evidence_count"]
        assert [item.title for item in document.candidates] == [
            item["title"] for item in expected["candidates_in_order"]
        ]
        inventory = report["reference_inventory"]
        assert len(document.evidence) == inventory["evidence_records"]
        assert len(document.ticker_items) == inventory["ticker_primary_items"]
        assert len(document.kpi_tiles) == inventory["kpi_tiles"]
        assert document.calendar_events == ()


def test_playbook_calendar_override_is_one_client_neutral_contract():
    calendar = _manifest()["calendar_contract"]
    assert calendar["reference_artifacts_contain_calendar_content"] is False
    assert calendar["replacement_section_id"] == "calendar"
    assert calendar["replacement_heading"] == "Federal opportunity calendar"
    assert calendar["forbidden_legacy_section_id"] == "leadership"
    assert calendar["date_statuses"] == [item.value for item in DateStatus]
    assert calendar["client_event_query_family_count"] == len(
        CLIENT_EVENT_QUERY_FAMILIES
    ) == 7
    assert calendar["client_event_source_class_count"] == len(
        CLIENT_EVENT_SOURCE_CLASSES
    ) == 4
    assert calendar["confirmed_event_horizon_months"] == 12
    assert calendar["official_flagship_horizon_months"] == 18
    assert calendar["procurement_research_horizon_months"] == 24
    assert calendar["official_event_links_required"] is True
    assert calendar["agency_attendance_requires_exact_official_assertion"] is True
    assert calendar["failed_and_zero_result_searches_are_distinct"] is True
    assert calendar["client_specific_calendar_branching_allowed"] is False
