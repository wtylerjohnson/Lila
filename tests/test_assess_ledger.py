"""Operational Assess-run adapters: strict live census, coverage, persistence."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.assess.approval import required_blocker_manifest
from agents.assess.contracts import (
    CoverageStatus, EvidenceUse, IntelligenceStatus, LiveClassification,
    LiveRecommendation, SourceLane,
)
from agents.assess.ledger import (
    AssessLedgerError, build_assess_run, load_current_assess_run,
    persist_assess_run,
)
from tools.capability import CapabilityTerms, ClientProfile


NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
BINDING = {
    "version": 1,
    "scope_designator": "all",
    "sweep_artifact": "searches_testco.json",
    "sweep_sha256": "sweep-sha",
    "profile_sha256": "profile-sha",
}


def _profile() -> ClientProfile:
    return ClientProfile(
        client_name="Testco",
        capability_terms=CapabilityTerms(
            core=["packet capture"], adjacent=["network visibility"],
            excluded=["janitorial"],
        ),
        mission_components=["CISA"],
        naics_boundary=["541512"],
    )


def _notice(source_id: str, title: str, *, notice_type="Solicitation",
            solicitation=None, office=None, deadline="2026-08-01",
            active="Yes", source="sam.gov") -> dict:
    raw = {
        "type": notice_type, "active": active, "agency": "DHS",
        "notice_id": source_id, "description_snippet": title,
    }
    if solicitation:
        raw["solicitation"] = solicitation
    if office:
        raw["office"] = office
    return {
        "source": source,
        "source_id": source_id,
        "title": title,
        "agency": "Department of Homeland Security / CISA",
        "naics_code": "541512",
        "posted_date": "2026-07-01",
        "response_deadline": deadline,
        "api_url": f"https://sam.gov/opp/{source_id}/view",
        "raw_payload": raw,
    }


def _sweep() -> dict:
    return {
        "client": "Testco",
        "generated_at": NOW.isoformat(),
        "search_scope": {"all": True},
        "results": {
            "sam.gov": [
                _notice("N1", "Packet capture platform"),
                _notice("N2", "Janitorial services"),
                _notice("N3", "Network visibility RFI",
                        notice_type="Sources Sought", deadline=None),
            ],
            "sam_census": {
                "matched": 3, "active_screened": 300,
                "complete": True, "source": "sam_extract",
            },
            "triage": {
                "N1": {"verdict": "pursue", "reason": "core product"},
                "N2": {"verdict": "discard", "reason": "wrong lane"},
                "N3": {"verdict": "monitor", "reason": "early buyer signal"},
            },
        },
    }


def _depth_record(notice_id="N1", *, attachments=None,
                  resources_checked=True, attachments_reviewed=False) -> dict:
    attachments = [] if attachments is None else attachments
    manifest = hashlib.sha256(json.dumps(
        attachments, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode()).hexdigest()
    return {
        "id": notice_id,
        "title": "Packet capture platform",
        "source_depth": {
            "notice_id": notice_id,
            "source_url": f"https://sam.gov/opp/{notice_id}/view",
            "retrieved_at": "2026-07-10T11:00:00+00:00",
            "description": (
                "The contractor shall provide an enterprise packet capture "
                "platform for CISA network operations."),
            "attachments": attachments,
            "description_checked": True,
            "resources_checked": resources_checked,
            "resources_schema": (
                "recognized_v1" if resources_checked else None),
            "attachment_inventory_count": (
                len(attachments) if resources_checked else None),
            "attachment_inventory_hash": (
                manifest if resources_checked else None),
            "attachments_reviewed": attachments_reviewed,
            "errors": [],
        },
    }


def _build(searches: dict):
    return build_assess_run(
        "Testco", searches, _profile(), BINDING, as_of=NOW)


def _requirement_review_payload(searches: dict, notice_id="N1", *,
                                attachments_reviewed=False) -> dict:
    proposed, _, _ = _build(searches)
    record = next(row for row in proposed.live.records
                  if row.notice_id == notice_id)
    assert record.requirement_excerpt
    evidence = next(
        row for row in reversed(record.authoritative_evidence)
        if record.requirement_excerpt in row.excerpt)
    return {
        "schema_version": 1,
        "client": "Testco",
        "binding": dict(BINDING),
        "reviews": [{
            "notice_id": notice_id,
            "evidence_id": evidence.evidence_id,
            "excerpt": record.requirement_excerpt,
            "capability_terms": ["packet capture"],
            "decision": "approved",
            "attachment_inventory_count": record.attachment_inventory_count,
            "attachment_inventory_hash": record.attachment_inventory_hash,
            "attachments_reviewed": attachments_reviewed,
            "reviewed_by": "operator",
            "reviewed_at": NOW.isoformat(),
        }],
    }


def _build_with_requirement_review(searches: dict, notice_id="N1", *,
                                   attachments_reviewed=False):
    payload = _requirement_review_payload(
        searches, notice_id, attachments_reviewed=attachments_reviewed)
    return build_assess_run(
        "Testco", searches, _profile(), BINDING,
        requirement_reviews_payload=payload, as_of=NOW)


def _horizon_payload(*, status="approved", schema_version=2) -> dict:
    source = "https://apfs-cloud.dhs.gov/api/forecast/record-1"
    text = (
        "DHS forecast line: packet capture modernization at CISA, anticipated "
        "FY2027 solicitation.")
    payload = {
        "client": "Testco",
        "status": status,
        "schema_version": schema_version,
        "generated_at": NOW.isoformat(),
        "binding": dict(BINDING),
        "fact_bank": [{
            "id": "H1", "text": text, "source": source,
            "kind": "forecast_line", "retrieved_at": NOW.isoformat(),
            "scope": {
                "kind": "agency",
                "agencies": ["Department of Homeland Security"],
                "component": "CISA",
            },
        }],
        "set": {
            "client_name": "Testco",
            "method_note": "DHS acquisition planning records screened.",
            "refine_note": "",
            "items": [{
                "id": "cisa-packet-capture",
                "title": "CISA packet capture modernization",
                "where": "Department of Homeland Security / CISA",
                "verified": [{
                    "evidence_id": "H1", "text": text, "source": source,
                }],
                "pattern": "Agency-stated planning identifies a requirement and timing.",
                "projection": (
                    "We judge CISA may compete packet capture modernization in FY2027."),
                "window": "FY2027",
                "watching": "forecast status changes; reviewed weekly",
                "confidence": "moderate",
                "lifecycle_stage": "acquisition_planning",
                "falsifier": (
                    "CISA removes the line or states that no procurement will occur."),
                "watch_trigger": "forecast status or anticipated solicitation changes",
                "monitoring_cadence": "reviewed weekly",
            }],
        },
        "rounds": [],
    }
    if status == "approved":
        payload["approved_at"] = NOW.isoformat()
        payload["approved_by"] = "operator"
    if schema_version == 1:
        payload.pop("schema_version")
        for fact in payload["fact_bank"]:
            fact.pop("retrieved_at")
        for item in payload["set"]["items"]:
            for field in ("lifecycle_stage", "falsifier", "watch_trigger",
                          "monitoring_cadence"):
                item.pop(field)
            item["verified"][0].pop("evidence_id")
    return payload


def _sam_monitor_horizon_payload() -> dict:
    payload = _horizon_payload()
    source = "https://sam.gov/opp/N3/view"
    text = (
        'Sources Sought: "Network visibility RFI" · '
        "Department of Homeland Security / CISA")
    payload["fact_bank"][0].update(
        text=text, source=source, kind="sam_monitor")
    item = payload["set"]["items"][0]
    item.update(
        title="CISA network visibility market research",
        pattern="A CISA sources-sought notice identifies active market research.",
        projection=(
            "We judge CISA may continue market research before deciding whether "
            "to procure."),
        lifecycle_stage="early_signal",
        falsifier="CISA cancels the notice without related follow-on activity.",
        watch_trigger="a CISA amendment or related planning notice appears",
        monitoring_cadence="reviewed weekly",
        watching=(
            "a CISA amendment or related planning notice appears; reviewed weekly"),
    )
    item["verified"][0].update(text=text, source=source)
    return payload


def _partner_document(*, blocker="ADJACENCY", candidate="PrimeCo",
                      source_id="N3"):
    play = SimpleNamespace(
        source_id=source_id,
        blockers=[SimpleNamespace(
            kind=blocker, fired=True,
            basis=f"opportunity-specific {blocker.lower()} blocker")],
        candidates=[SimpleNamespace(
            name=candidate, directions=["SUB_TO_PRIME"], dual_role=None)],
    )
    return SimpleNamespace(
        partnering=SimpleNamespace(plays=[play]))


def _add_partner_edge(searches: dict, *, agency="Department of Homeland Security",
                      subagency="CISA",
                      description="Packet capture engineering and operations") -> None:
    searches["results"]["sam.gov"][2]["title"] = "Packet capture RFI"
    searches["results"]["sam.gov"][2]["raw_payload"][
        "description_snippet"] = "Packet capture market research"
    searches["results"]["subawards"] = {"edges": {"541512": [{
        "subaward_id": "SA-1",
        "prime": "PrimeCo",
        "sub": "DeliverySub",
        "prime_award_id": "PRIME-1",
        "amount": 500_000.0,
        "date": "2026-05-01",
        "description": description,
        "awarding_agency": agency,
        "awarding_sub_agency": subagency,
        "source": "https://api.usaspending.gov/api/v2/search/spending_by_award/",
    }]}}


def test_metadata_only_pursue_is_retained_but_never_bid_now():
    run, diagnostics, posting_index = _build(_sweep())
    assert diagnostics == ()
    assert len(run.live.records) == 3
    by_notice = {record.notice_id: record for record in run.live.records}
    assert by_notice["N1"].classification == LiveClassification.UNSCREENED
    assert by_notice["N1"].recommendation == LiveRecommendation.RESEARCH
    assert "not retained" in by_notice["N1"].attachment_gap
    assert by_notice["N2"].classification == LiveClassification.UNSCREENED
    assert by_notice["N2"].recommendation == LiveRecommendation.RESEARCH
    assert "Metadata-only triage recommended discard" in \
        by_notice["N2"].attachment_gap
    assert by_notice["N3"].classification == LiveClassification.MARKET_RESEARCH
    assert posting_index["N1"] == by_notice["N1"].record_id
    assert not any(record.classification == LiveClassification.BID_NOW
                   for record in run.live.records)


def test_complete_zero_result_sam_census_is_valid_complete_coverage():
    searches = _sweep()
    searches["results"]["sam.gov"] = []
    searches["results"]["triage"] = {}
    searches["results"]["sam_census"].update(
        matched=0, active_screened=0)

    run, diagnostics, posting_index = _build(searches)
    assert diagnostics == ()
    assert run.live.records == ()
    assert posting_index == {}
    sam = next(row for row in run.coverage
               if row.lane == SourceLane.LIVE and row.source == "sam.gov")
    assert sam.status == CoverageStatus.COMPLETE
    assert sam.records_screened == 0
    assert sam.total_available == 0


@pytest.mark.parametrize(("missing", "expected"), [
    ("source", "collector source is absent or unrecognized"),
    ("matched", "matched count is absent or invalid"),
    ("active_screened", "active-screened count is absent or invalid"),
])
def test_sam_census_missing_source_or_counts_never_claims_complete(
        missing, expected):
    searches = _sweep()
    searches["results"]["sam_census"].pop(missing)

    run, _, _ = _build(searches)
    sam = next(row for row in run.coverage
               if row.lane == SourceLane.LIVE and row.source == "sam.gov")
    assert sam.status == CoverageStatus.PARTIAL
    assert expected in sam.note
    assert sam in run.blocking_sources()


def test_mismatched_sam_matched_count_blocks_an_otherwise_bid_now_record():
    searches = _sweep()
    searches["results"]["sam_census"]["matched"] = 2
    searches["results"]["dossiers"] = {"records": [_depth_record()]}

    run, _, _ = _build_with_requirement_review(searches)
    record = next(row for row in run.live.records if row.notice_id == "N1")
    assert record.classification == LiveClassification.UNSCREENED
    assert "Required SAM source census is incomplete" in record.attachment_gap
    sam = next(row for row in run.coverage if row.source == "sam.gov")
    assert sam.status == CoverageStatus.PARTIAL
    assert "matched count 2 does not reconcile to 3 rows" in sam.note


def test_live_api_omitted_pass_manifest_blocks_bid_now_promotion():
    searches = _sweep()
    searches["results"]["sam_census"] = {
        "matched": 3,
        "active_screened": 3,
        "retrieved": 3,
        "complete": True,
        "source": "sam.gov live API",
        "passes": {
            "all-active": {
                "retrieved": 3, "total_records": 3, "complete": True,
            },
        },
        "attempt_manifest": {
            "requested": [{"id": "all-active"}, {"id": "keyword-pass"}],
            "executed": [{"id": "all-active"}],
            "omitted": [{"id": "keyword-pass"}],
        },
    }
    searches["results"]["dossiers"] = {"records": [_depth_record()]}

    run, _, _ = _build_with_requirement_review(searches)
    record = next(row for row in run.live.records if row.notice_id == "N1")
    assert record.classification == LiveClassification.UNSCREENED
    assert "Required SAM source census is incomplete" in record.attachment_gap
    sam = next(row for row in run.coverage if row.source == "sam.gov")
    assert sam.status == CoverageStatus.PARTIAL
    assert "1 requested live-API pass(es) were omitted" in sam.note


def test_retained_source_text_and_confirmed_zero_attachments_can_be_bid_now():
    searches = _sweep()
    searches["results"]["dossiers"] = {"records": [_depth_record()]}
    run, _, _ = _build_with_requirement_review(searches)
    record = next(row for row in run.live.records if row.notice_id == "N1")
    assert record.classification == LiveClassification.BID_NOW
    assert record.recommendation == LiveRecommendation.PURSUE
    assert record.attachments_checked is True
    assert record.attachment_gap is None
    assert "packet capture" in record.requirement_excerpt.lower()
    assert record.fit_trace and record.authoritative_evidence[0].evidence_id \
        in record.fit_trace[0]


def test_long_description_candidate_stays_exact_and_needs_human_review():
    requirement = (
        "The contractor shall provide an enterprise packet capture platform "
        "for CISA network operations.")
    description = ("Background acquisition context. " * 50) + requirement
    assert description.index(requirement) > 800
    searches = _sweep()
    depth = _depth_record()
    depth["source_depth"]["description"] = description
    searches["results"]["dossiers"] = {"records": [depth]}

    proposed, _, _ = _build(searches)
    candidate = next(row for row in proposed.live.records
                     if row.notice_id == "N1")
    assert candidate.requirement_excerpt == requirement
    assert candidate.requirement_excerpt in candidate.authoritative_evidence[-1].excerpt
    assert candidate.classification == LiveClassification.UNSCREENED
    assert "awaits explicit human approval" in candidate.attachment_gap

    reviewed, _, _ = _build_with_requirement_review(searches)
    promoted = next(row for row in reviewed.live.records
                    if row.notice_id == "N1")
    assert promoted.classification == LiveClassification.BID_NOW
    assert promoted.requirement_excerpt == requirement
    assert promoted.requirement_excerpt in promoted.authoritative_evidence[-1].excerpt
    assert promoted.requirement_reviewed_by == "operator"


def test_attachment_bearing_bid_now_requires_matching_human_inventory_review():
    searches = _sweep()
    attachments = [{
        "name": "PWS.pdf",
        "type": "application/pdf",
        "resource_id": "R1",
    }]
    searches["results"]["dossiers"] = {
        "records": [_depth_record(attachments=attachments)],
    }

    reviewed, diagnostics, _ = _build_with_requirement_review(
        searches, attachments_reviewed=True)
    assert diagnostics == ()
    promoted = next(row for row in reviewed.live.records
                    if row.notice_id == "N1")
    assert promoted.classification == LiveClassification.BID_NOW
    assert promoted.attachments_checked is True
    assert promoted.attachment_reviewed_by == "operator"
    assert promoted.attachment_reviewed_at == NOW
    assert promoted.attachment_inventory_hash

    stale_review = _requirement_review_payload(
        searches, attachments_reviewed=True)
    stale_review["reviews"][0]["attachment_inventory_hash"] = "stale-hash"
    blocked, blocked_diagnostics, _ = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        requirement_reviews_payload=stale_review, as_of=NOW)
    record = next(row for row in blocked.live.records if row.notice_id == "N1")
    assert record.classification == LiveClassification.UNSCREENED
    assert record.attachment_reviewed_by is None
    assert "reviewed attachment inventory hash no longer matches" \
        in record.attachment_gap
    assert any("reviewed attachment inventory hash no longer matches" in message
               for message in blocked_diagnostics)


def test_open_attachment_or_incomplete_sam_census_blocks_bid_now():
    searches = _sweep()
    searches["results"]["dossiers"] = {"records": [
        _depth_record(attachments=[{"name": "PWS.pdf", "type": "application/pdf"}])
    ]}
    run, _, _ = _build_with_requirement_review(searches)
    record = next(row for row in run.live.records if row.notice_id == "N1")
    assert record.classification == LiveClassification.UNSCREENED
    assert "attachments have not been human-reviewed" in record.attachment_gap

    incomplete = copy.deepcopy(searches)
    incomplete["results"]["dossiers"] = {"records": [
        _depth_record(attachments=[])]}
    incomplete["results"]["sam_census"]["complete"] = False
    run2, _, _ = _build_with_requirement_review(incomplete)
    record2 = next(row for row in run2.live.records if row.notice_id == "N1")
    assert record2.classification == LiveClassification.UNSCREENED
    assert "SAM source census is incomplete" in record2.attachment_gap
    sam = next(row for row in run2.coverage
               if row.lane == SourceLane.LIVE and row.source == "sam.gov")
    assert sam.required and sam.status == CoverageStatus.PARTIAL


def test_negated_capability_or_mismatched_source_identity_cannot_be_bid_now():
    negated = _sweep()
    record = _depth_record()
    record["source_depth"]["description"] = (
        "This solicitation is solely for cafeteria services. "
        "Packet capture is not required and is explicitly out of scope.")
    negated["results"]["dossiers"] = {"records": [record]}
    run, _, _ = _build(negated)
    live = next(row for row in run.live.records if row.notice_id == "N1")
    assert live.classification == LiveClassification.UNSCREENED
    assert "explicit positive requirement language" in live.attachment_gap

    mismatched = _sweep()
    bad = _depth_record()
    bad["source_depth"]["source_url"] = "https://sam.gov/opp/OTHER/view"
    mismatched["results"]["sam.gov"][0]["raw_payload"]["notice_id"] = "OTHER"
    mismatched["results"]["dossiers"] = {"records": [bad]}
    run2, diagnostics2, _ = _build(mismatched)
    assert not any(row.notice_id == "N1" for row in run2.live.records)
    assert any("source/raw identity" in message for message in diagnostics2)
    sam = next(row for row in run2.coverage if row.source == "sam.gov")
    assert sam.status == CoverageStatus.PARTIAL


def test_failed_description_lineage_blocks_even_when_stale_text_remains():
    searches = _sweep()
    record = _depth_record()
    record["source_depth"]["description_checked"] = False
    record["source_depth"]["errors"] = [
        "description fetch failed: stale cache only"]
    searches["results"]["dossiers"] = {"records": [record]}
    run, _, _ = _build(searches)
    live = next(row for row in run.live.records if row.notice_id == "N1")
    assert live.classification == LiveClassification.UNSCREENED
    assert "description fetch is not marked complete" in live.attachment_gap
    assert "description fetch carries an open error" in live.attachment_gap


def test_same_day_deadline_and_source_less_legacy_row_fail_closed():
    searches = _sweep()
    searches["results"]["sam.gov"][0]["response_deadline"] = "2026-07-10"
    searches["results"]["sam.gov"][0]["source"] = ""
    searches["results"]["dossiers"] = {"records": [_depth_record()]}
    run, diagnostics, _ = _build(searches)
    assert not any(row.notice_id == "N1" for row in run.live.records)
    assert any("source/raw identity" in message for message in diagnostics)

    same_day = _sweep()
    same_day["results"]["sam.gov"][0]["response_deadline"] = "2026-07-10"
    same_day["results"]["dossiers"] = {"records": [_depth_record()]}
    run2, _, _ = _build(same_day)
    record = next(row for row in run2.live.records if row.notice_id == "N1")
    assert record.classification == LiveClassification.UNSCREENED
    assert "future deadline" in record.attachment_gap


@pytest.mark.parametrize("raw_scope", [
    None,
    {"all": True, "mode": "focus", "agencies": ["DHS"]},
    {"mode": "focus", "agencies": []},
    {"all": False, "agencies": ["DHS"]},
])
def test_sweep_scope_must_explicitly_choose_all_or_valid_focus(raw_scope):
    searches = _sweep()
    if raw_scope is None:
        searches.pop("search_scope")
    else:
        searches["search_scope"] = raw_scope
    with pytest.raises(AssessLedgerError):
        _build(searches)


def test_focus_scope_rejects_out_of_scope_live_and_horizon_rows():
    searches = _sweep()
    searches["search_scope"] = {
        "mode": "focus",
        "agencies": [{
            "name": "Department of Homeland Security", "abbr": "DHS",
        }],
    }
    searches["results"]["sam.gov"][1]["agency"] = (
        "Department of Defense / DISA")
    searches["results"]["sam.gov"][1]["raw_payload"]["agency"] = "DOD"
    binding = dict(BINDING, scope_designator="agency_dhs")
    horizon = _horizon_payload()
    horizon["binding"] = dict(binding)
    horizon["set"]["items"][0]["where"] = "Department of Defense / DISA"

    run, diagnostics, posting_index = build_assess_run(
        "Testco", searches, _profile(), binding,
        horizon_payload=horizon, as_of=NOW)
    assert "N2" not in posting_index
    assert all(record.notice_id != "N2" for record in run.live.records)
    assert run.horizon.items == ()
    assert any("N2: buyer is outside" in message for message in diagnostics)
    assert any("outside the operator-selected agency scope" in message
               for message in diagnostics)
    sam = next(row for row in run.coverage if row.source == "sam.gov")
    assert sam.status == CoverageStatus.PARTIAL


def test_focus_scope_rejects_in_scope_horizon_label_with_out_of_scope_fact():
    searches = _sweep()
    searches["search_scope"] = {
        "mode": "focus",
        "agencies": [{
            "name": "Department of Homeland Security", "abbr": "DHS",
        }],
    }
    binding = dict(BINDING, scope_designator="agency_dhs")
    horizon = _horizon_payload()
    horizon["binding"] = dict(binding)
    in_scope, in_scope_diagnostics, _ = build_assess_run(
        "Testco", searches, _profile(), binding,
        horizon_payload=horizon, as_of=NOW)
    assert len(in_scope.horizon.items) == 1
    assert not any("Horizon evidence H1" in message
                   for message in in_scope_diagnostics)

    missing_scope = copy.deepcopy(horizon)
    missing_scope["fact_bank"][0].pop("scope")
    missing_run, missing_diagnostics, _ = build_assess_run(
        "Testco", searches, _profile(), binding,
        horizon_payload=missing_scope, as_of=NOW)
    assert missing_run.horizon.items == ()
    assert any("lacks structured buyer-scope metadata" in message
               for message in missing_diagnostics)

    # The item label stays in scope. Only the exact cited evidence reveals that
    # this is a VA signal, which must not be laundered into a DHS thesis.
    horizon["fact_bank"][0]["scope"] = {
        "kind": "agency",
        "agencies": ["Department of Veterans Affairs"],
        "component": "Veterans Health Administration",
    }

    run, diagnostics, _ = build_assess_run(
        "Testco", searches, _profile(), binding,
        horizon_payload=horizon, as_of=NOW)

    assert run.horizon.items == ()
    assert any(
        "Horizon evidence H1 belongs to an agency outside" in message
        for message in diagnostics
    )
    gate = next(row for row in run.coverage
                if row.source == "horizon_thesis_gate")
    assert gate.status == CoverageStatus.FAILED


def test_all_federal_horizon_preserves_pre_scope_v2_artifact_behavior():
    horizon = _horizon_payload()
    horizon["fact_bank"][0].pop("scope")

    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=horizon, as_of=NOW)

    assert len(run.horizon.items) == 1
    assert diagnostics == ()


def test_amendment_family_uses_office_namespace_and_stable_posting_index():
    searches = _sweep()
    first = _notice(
        "A1", "Packet capture platform", solicitation="SOL-1", office="OFFICE-A")
    first["posted_date"] = "2026-07-01"
    second = _notice(
        "A2", "Packet capture platform amendment", solicitation="SOL-1",
        office="OFFICE-A")
    second["posted_date"] = "2026-07-02"
    other_office = _notice(
        "B1", "Different buyer", solicitation="SOL-1", office="OFFICE-B")
    searches["results"]["sam.gov"] = [other_office, second, first]
    searches["results"]["sam_census"]["matched"] = 3
    searches["results"]["triage"] = {
        "A1": {"verdict": "monitor", "reason": "early"},
        "A2": {"verdict": "pursue", "reason": "current"},
        "B1": {"verdict": "monitor", "reason": "other office"},
    }
    run, _, index = _build(searches)
    assert len(run.live.records) == 2
    assert index["A1"] == index["A2"]
    assert index["B1"] != index["A1"]
    family = next(record for record in run.live.records
                  if record.record_id == index["A1"])
    assert family.notice_id == "A2"
    assert len(family.authoritative_evidence) == 2


def test_source_coverage_never_calls_missing_or_capped_sources_complete():
    searches = _sweep()
    searches["results"]["subawards"] = {"edges": {"541512": []}}
    searches["results"]["watchdogs"] = {"error": "feed unavailable"}
    run, _, _ = _build(searches)
    by_key = {(row.lane, row.source): row for row in run.coverage}
    assert by_key[(SourceLane.LIVE, "sam.gov")].status == CoverageStatus.COMPLETE
    assert by_key[(SourceLane.PARTNER, "subawards")].status == CoverageStatus.PARTIAL
    assert by_key[(SourceLane.HORIZON, "watchdogs")].status == CoverageStatus.FAILED
    assert by_key[(SourceLane.HORIZON, "forecast_signals")].status \
        == CoverageStatus.PARTIAL
    assert "attempt unknown" in by_key[
        (SourceLane.HORIZON, "forecast_signals")].note
    for source in ("regulations_gov", "cisa_kev"):
        row = by_key[(SourceLane.HORIZON, source)]
        assert row.required is True
        assert row.status == CoverageStatus.PARTIAL
        assert "attempt unknown" in row.note
    blocking = {row.source for row in run.blocking_sources()}
    assert {"regulations_gov", "cisa_kev"} <= blocking
    manifest = required_blocker_manifest(run.coverage)
    manifest_sources = {row["source"] for row in manifest["rows"]}
    assert {"regulations_gov", "cisa_kev"} <= manifest_sources
    prior_coverage = tuple(
        row for row in run.coverage
        if row.source not in {"regulations_gov", "cisa_kev"}
    )
    assert manifest["sha256"] != required_blocker_manifest(
        prior_coverage)["sha256"]


def test_new_official_horizon_sources_fail_closed_and_never_claim_complete():
    searches = _sweep()
    searches["results"]["regulations_gov"] = {
        "error": "regulations.gov unavailable",
    }
    searches["results"]["cisa_kev"] = {
        "recent_count": 2,
        "matched": [{"cve": "CVE-2026-0001"}],
    }

    run, _, _ = _build(searches)
    by_source = {row.source: row for row in run.coverage}

    assert by_source["regulations_gov"].required is True
    assert by_source["regulations_gov"].status == CoverageStatus.FAILED
    assert by_source["regulations_gov"].note == "regulations.gov unavailable"
    assert by_source["cisa_kev"].required is True
    assert by_source["cisa_kev"].status == CoverageStatus.PARTIAL
    assert by_source["cisa_kev"].records_screened == 1
    assert {"regulations_gov", "cisa_kev"} <= {
        row.source for row in run.blocking_sources()
    }


def test_bound_horizon_v2_projects_exact_evidence_into_thesis_ledger():
    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=_horizon_payload(), as_of=NOW)
    assert diagnostics == ()
    assert len(run.horizon.items) == 1
    thesis = run.horizon.items[0]
    assert thesis.status == IntelligenceStatus.APPROVED
    assert thesis.lifecycle_stage.value == "acquisition_planning"
    assert thesis.evidence[0].excerpt.startswith("DHS forecast line")
    assert thesis.evidence[0].primary_source is True
    assert thesis.falsifier.startswith("CISA removes")
    gate = next(row for row in run.coverage
                if row.source == "horizon_thesis_gate")
    assert gate.status == CoverageStatus.COMPLETE


def test_cross_client_future_horizon_and_review_cannot_advance_run_as_of():
    searches = _sweep()
    searches["results"]["dossiers"] = {"records": [_depth_record()]}
    future = NOW + timedelta(days=3650)

    horizon = _horizon_payload()
    horizon["client"] = "OtherCo"
    horizon["set"]["client_name"] = "OtherCo"
    horizon["generated_at"] = future.isoformat()
    horizon["approved_at"] = future.isoformat()
    horizon["fact_bank"][0]["retrieved_at"] = future.isoformat()

    reviews = _requirement_review_payload(searches)
    reviews["client"] = "OtherCo"
    reviews["reviews"][0]["reviewed_at"] = future.isoformat()

    run, diagnostics, _ = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        horizon_payload=horizon,
        requirement_reviews_payload=reviews,
        as_of=NOW)
    assert run.as_of == NOW
    assert run.horizon.items == ()
    live = next(row for row in run.live.records if row.notice_id == "N1")
    assert live.requirement_reviewed_at is None
    assert live.verified_at == NOW
    assert all(evidence.retrieved_at is None or evidence.retrieved_at <= NOW
               for record in run.live.records
               for evidence in record.authoritative_evidence)
    assert any("Horizon artifact belongs to a different client" in message
               for message in diagnostics)
    assert any("Live requirement reviews belong to a different client" in message
               for message in diagnostics)


def test_metadata_only_sam_monitor_never_projects_requirement_support():
    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=_sam_monitor_horizon_payload(), as_of=NOW)
    assert diagnostics == ()
    evidence = run.horizon.items[0].evidence[0]
    assert set(evidence.supports) == {
        EvidenceUse.TIMING, EvidenceUse.BUYER,
    }
    assert EvidenceUse.REQUIREMENT not in evidence.supports


def test_sam_monitor_triage_laundering_fails_strict_ledger_projection():
    payload = _sam_monitor_horizon_payload()
    laundered = (
        payload["fact_bank"][0]["text"]
        + " · triage: strong capability match")
    payload["fact_bank"][0]["text"] = laundered
    payload["set"]["items"][0]["verified"][0]["text"] = laundered
    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=payload, as_of=NOW)
    assert run.horizon.items == ()
    assert any("non-source triage analysis" in message
               for message in diagnostics)


def test_horizon_inner_client_mismatch_fails_strict_ledger_projection():
    payload = _horizon_payload()
    payload["set"]["client_name"] = "OtherCo"
    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=payload, as_of=NOW)
    assert run.horizon.items == ()
    assert any("client_name does not match" in message
               for message in diagnostics)


def test_duplicate_horizon_item_ids_degrade_to_strict_diagnostic():
    payload = _horizon_payload()
    payload["set"]["items"].append(
        copy.deepcopy(payload["set"]["items"][0]))
    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=payload, as_of=NOW)
    assert run.horizon.items == ()
    assert any("item id 'cisa-packet-capture' is duplicated" in message
               for message in diagnostics)


def test_horizon_draft_is_proposed_and_v1_or_stale_artifacts_fail_closed():
    draft = _horizon_payload(status="draft")
    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=draft, as_of=NOW)
    assert diagnostics == ()
    assert run.horizon.items[0].status == IntelligenceStatus.PROPOSED
    gate = next(row for row in run.coverage
                if row.source == "horizon_thesis_gate")
    assert gate.status == CoverageStatus.PARTIAL

    legacy = _horizon_payload(schema_version=1)
    run2, diagnostics2, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=legacy, as_of=NOW)
    assert run2.horizon.items == ()
    assert any("cannot be losslessly projected" in message
               for message in diagnostics2)

    stale = _horizon_payload()
    stale["binding"]["sweep_sha256"] = "changed"
    run3, diagnostics3, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=stale, as_of=NOW)
    assert run3.horizon.items == ()
    assert any("binding differs" in message for message in diagnostics3)


def test_horizon_evidence_id_mismatch_rejects_whole_thesis_set():
    payload = _horizon_payload()
    payload["set"]["items"][0]["verified"][0]["evidence_id"] = "MISSING"
    run, diagnostics, _ = build_assess_run(
        "Testco", _sweep(), _profile(), BINDING,
        horizon_payload=payload, as_of=NOW)
    assert run.horizon.items == ()
    assert any("failed strict validation" in message for message in diagnostics)
    gate = next(row for row in run.coverage
                if row.source == "horizon_thesis_gate")
    assert gate.status == CoverageStatus.FAILED


def test_capability_matched_subaward_maps_to_exact_live_partner_path():
    searches = _sweep()
    _add_partner_edge(searches)
    run, diagnostics, posting_index = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        document=_partner_document(), as_of=NOW)
    assert diagnostics == ()
    assert len(run.partners.items) == 1
    partner = run.partners.items[0]
    assert partner.partner_name == "PrimeCo"
    assert partner.linked_assess_ids == (posting_index["N3"],)
    assert partner.direction.value == "sub_to_prime"
    assert partner.status == IntelligenceStatus.RESEARCH_NEEDED
    assert partner.evidence[0].kind.value == "subaward"
    assert "Packet capture" in partner.evidence[0].excerpt
    assert "capability" in {use.value for use in partner.evidence[0].supports}
    coverage = next(row for row in run.coverage
                    if row.source == "partner_subaward_capability")
    assert coverage.required and coverage.status == CoverageStatus.PARTIAL


def test_same_naics_award_or_wrong_buyer_never_qualifies_a_partner():
    searches = _sweep()
    searches["results"]["usaspending.gov"] = [{
        "naics_code": "541512",
        "awards": [{
            "award_id": "A1", "recipient": "PrimeCo", "amount": 9_000_000,
            "awarding_agency": "Department of Homeland Security",
            "url": "https://www.usaspending.gov/award/A1",
        }],
    }]
    searches["results"]["sam.gov"][2]["title"] = "Packet capture RFI"
    run, _, _ = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        document=_partner_document(), as_of=NOW)
    assert run.partners.items == ()

    wrong_buyer = _sweep()
    _add_partner_edge(
        wrong_buyer, agency="Department of Defense", subagency="DISA")
    run2, _, _ = build_assess_run(
        "Testco", wrong_buyer, _profile(), BINDING,
        document=_partner_document(), as_of=NOW)
    assert run2.partners.items == ()

    wrong_component = _sweep()
    _add_partner_edge(wrong_component, subagency="TSA")
    run3, _, _ = build_assess_run(
        "Testco", wrong_component, _profile(), BINDING,
        document=_partner_document(), as_of=NOW)
    assert run3.partners.items == ()


def test_vehicle_blocker_yields_required_source_gap_not_a_fake_partner():
    searches = _sweep()
    _add_partner_edge(searches)
    run, diagnostics, _ = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        document=_partner_document(blocker="VEHICLE"), as_of=NOW)
    assert run.partners.items == ()
    assert any("unregistered blocker evidence: VEHICLE" in message
               for message in diagnostics)
    holders = next(row for row in run.coverage
                   if row.source == "partner_vehicle_holders")
    assert holders.required and holders.status == CoverageStatus.NOT_REGISTERED


def test_partner_ids_and_evidence_are_stable_under_edge_reordering():
    first = _sweep()
    _add_partner_edge(first)
    second_edge = copy.deepcopy(
        first["results"]["subawards"]["edges"]["541512"][0])
    second_edge.update(
        subaward_id="SA-2", date="2026-06-01", amount=750_000.0)
    first["results"]["subawards"]["edges"]["541512"].append(second_edge)
    second = copy.deepcopy(first)
    second["results"]["subawards"]["edges"]["541512"].reverse()

    run1, _, _ = build_assess_run(
        "Testco", first, _profile(), BINDING,
        document=_partner_document(), as_of=NOW)
    run2, _, _ = build_assess_run(
        "Testco", second, _profile(), BINDING,
        document=_partner_document(), as_of=NOW)
    left, right = run1.partners.items[0], run2.partners.items[0]
    assert left.partner_id == right.partner_id
    assert [row.evidence_id for row in left.evidence] \
        == [row.evidence_id for row in right.evidence]


def test_repeated_native_subaward_id_under_distinct_prime_awards_never_collides():
    searches = _sweep()
    _add_partner_edge(searches)
    repeated = copy.deepcopy(
        searches["results"]["subawards"]["edges"]["541512"][0])
    repeated.update(
        prime_award_id="PRIME-2", sub="OtherDeliverySub",
        amount=750_000.0)
    searches["results"]["subawards"]["edges"]["541512"].append(repeated)

    run, diagnostics, _ = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        document=_partner_document(), as_of=NOW)

    assert diagnostics == ()
    evidence = run.partners.items[0].evidence
    assert len(evidence) == 2
    assert len({row.evidence_id for row in evidence}) == 2
    assert all(row.evidence_id.startswith(
        "ev:usaspending:subaward:v1:") for row in evidence)


def test_assess_run_persistence_is_immutable_atomic_and_validated(tmp_path):
    run, diagnostics, index = _build(_sweep())
    path = persist_assess_run(
        run, BINDING, diagnostics, index, state_dir=tmp_path)
    assert path.exists()
    loaded = load_current_assess_run("Testco", state_dir=tmp_path)
    assert loaded and loaded["run"]["run_id"] == run.run_id
    assert loaded["posting_index"] == dict(sorted(index.items()))

    # Re-materializing identical evidence is idempotent and does not mint a
    # different run or advance the current pointer's freshness boundary.
    pointer = next(tmp_path.rglob("all.current.json"))
    pointer_mtime_ns = pointer.stat().st_mtime_ns
    assert persist_assess_run(
        run, BINDING, diagnostics, index, state_dir=tmp_path) == path
    assert pointer.stat().st_mtime_ns == pointer_mtime_ns
    assert not list(tmp_path.rglob("*.tmp"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["diagnostics"].append("tampered")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_current_assess_run("Testco", state_dir=tmp_path) is None
    assert json.loads(pointer.read_text())["run_id"] == run.run_id


def test_run_identity_covers_cutoff_inputs_diagnostics_and_is_recomputed(
        tmp_path):
    searches = _sweep()
    base, base_diagnostics, base_index = _build(searches)
    later, _, _ = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        as_of=NOW + timedelta(minutes=5))
    inputs = {"horizon_sha256": "horizon-v2", "review_sha256": "review-v1"}
    with_inputs, input_diagnostics, input_index = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        projection_inputs=inputs, as_of=NOW)
    with_diagnostic, diagnostic_messages, diagnostic_index = build_assess_run(
        "Testco", searches, _profile(), BINDING,
        extra_diagnostics=("adversarial synthetic gap",), as_of=NOW)

    assert base_diagnostics == ()
    assert len({
        base.run_id, later.run_id, with_inputs.run_id, with_diagnostic.run_id,
    }) == 4

    with pytest.raises(AssessLedgerError,
                       match="does not match its persisted evidence projection"):
        persist_assess_run(
            with_inputs, BINDING, input_diagnostics, input_index,
            state_dir=tmp_path)
    input_path = persist_assess_run(
        with_inputs, BINDING, input_diagnostics, input_index, inputs,
        state_dir=tmp_path)
    assert input_path.exists()
    loaded = load_current_assess_run("Testco", state_dir=tmp_path)
    assert loaded and loaded["run"]["run_id"] == with_inputs.run_id

    with pytest.raises(AssessLedgerError,
                       match="does not match its persisted evidence projection"):
        persist_assess_run(
            with_diagnostic, BINDING, (), diagnostic_index,
            state_dir=tmp_path)
    diagnostic_path = persist_assess_run(
        with_diagnostic, BINDING, diagnostic_messages, diagnostic_index,
        state_dir=tmp_path)
    assert diagnostic_path.exists()

    # Preserve a valid outer artifact checksum while changing the canonical
    # projection. Loading must still recompute identity and reject the forgery.
    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    payload["diagnostics"].append("forged post-persist diagnostic")
    diagnostic_path.write_text(json.dumps(payload), encoding="utf-8")
    pointer_path = next(tmp_path.rglob("all.current.json"))
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str)
    pointer["artifact_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    assert load_current_assess_run("Testco", state_dir=tmp_path) is None

    # Keep the otherwise-unused baseline index visible in this identity test:
    # it is also part of the persisted projection and must be deterministic.
    assert base_index == input_index == diagnostic_index
