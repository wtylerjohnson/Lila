"""Deterministic pre-triage keeps breadth without paying to judge NAICS noise."""
from __future__ import annotations

from agents.decisions.triage import deterministic_prefilter
from tools.relevance.taxonomy import CapabilityTaxonomy, KillRule


TAXONOMY = CapabilityTaxonomy(**{
    "client_name": "Arista Networks",
    "version": 2,
    "updated": "2026-08-14",
    "core": [
        {"term": "Arista EOS", "mode": "exact_phrase"},
        {"term": "data center switching", "mode": "stemmed"},
    ],
    "adjacent": [
        {"term": "network modernization", "mode": "stemmed"},
    ],
    "exclude": [
        {"term": "Arista Aviation Services", "scope": "record"},
    ],
    "code_universe": {"naics": ["541512"], "psc": []},
})


def test_capability_evidence_reaches_model_and_every_row_reconciles():
    notices = [
        {"source_id": "brand", "title": "Arista EOS support", "naics": "541512"},
        {"source_id": "demand", "title": "Data center switching refresh", "naics": "541512"},
        {"source_id": "adjacent", "title": "Network modernization", "naics": "541512"},
        {"source_id": "naics", "title": "Help desk staffing", "naics": "541512"},
        {"source_id": "collision", "title": "Arista Aviation Services", "naics": "541512"},
    ]

    candidates, ruled, receipt = deterministic_prefilter(notices, TAXONOMY)

    assert [row["source_id"] for row in candidates] == [
        "brand", "demand", "adjacent"]
    assert set(ruled) == {"naics", "collision"}
    assert ruled["collision"]["reason"].startswith("excluded by")
    assert receipt["candidate_census"] == 5
    assert receipt["model_candidates"] == 3
    assert receipt["model_candidates_before_thread_consolidation"] == 3
    assert receipt["model_candidates_after_solicitation_consolidation"] == 3
    assert receipt["superseded_revisions"] == 0
    assert receipt["cross_post_duplicates"] == 0
    assert receipt["model_candidate_reasons"] == {
        "adjacent-evidence": 1,
        "core-evidence": 2,
    }
    assert receipt["deterministic_discards"] == 2
    assert receipt["complete"] is True


def test_public_attachment_text_can_supply_core_evidence():
    notice = {
        "source_id": "attachment",
        "title": "Enterprise hardware refresh",
        "naics_code": "541512",
        "raw_payload": {"text": "The requirement includes Arista EOS support."},
    }

    candidates, ruled, _receipt = deterministic_prefilter([notice], TAXONOMY)

    assert candidates == [notice]
    assert ruled == {}


def test_full_description_screen_context_can_supply_adjacent_evidence():
    notice = {
        "source_id": "deep-description",
        "title": "Enterprise platform RFI",
        "naics_code": "999999",
        "raw_payload": {
            "description_snippet": "Generic opening language.",
            "screen_evidence_matches": [{
                "term": "network modernization",
                "field": "description",
                "matched_text": "network modernization",
                "context": (
                    "The agency plans a network modernization across its "
                    "sites."
                ),
            }],
        },
    }

    candidates, ruled, receipt = deterministic_prefilter([notice], TAXONOMY)

    assert candidates == [notice]
    assert ruled == {}
    assert receipt["model_candidate_reasons"] == {"adjacent-evidence": 1}


def test_span_kill_does_not_discard_independent_surviving_core_evidence():
    taxonomy = TAXONOMY.model_copy(deep=True)
    taxonomy.exclude.append(KillRule(
        term="legacy network monitoring",
        scope="span",
        reason="legacy-only phrase",
    ))
    notice = {
        "source_id": "mixed",
        "title": (
            "Legacy network monitoring " + "separate context " * 20
            + "data center switching refresh"
        ),
        "naics_code": "541512",
    }

    candidates, ruled, _receipt = deterministic_prefilter([notice], taxonomy)

    assert [row["source_id"] for row in candidates] == ["mixed"]
    assert ruled == {}


def test_solicitation_revisions_receive_one_model_judgment_with_lineage():
    notices = [
        {
            "source_id": "N-OLD",
            "title": "Data center switching RFI",
            "posted_date": "2026-07-01",
            "naics_code": "541512",
            "raw_payload": {
                "solicitation": "SOL-1",
                "agency": "Department of Energy",
                "office": "National Laboratory Procurement Office",
                "description_snippet": "Original switching requirement.",
            },
        },
        {
            "source_id": "N-NEW",
            "title": "Data center switching RFI amendment",
            "posted_date": "2026-07-15",
            "naics_code": "541512",
            "raw_payload": {
                "solicitation": "SOL-1",
                "agency": "Department of Energy",
                "office": "National Laboratory Procurement Office",
                "description_snippet": "Updated switching requirement.",
            },
        },
    ]

    candidates, ruled, receipt = deterministic_prefilter(notices, TAXONOMY)

    assert [row["source_id"] for row in candidates] == ["N-NEW"]
    assert ruled["N-OLD"]["superseded_by"] == "N-NEW"
    assert candidates[0]["raw_payload"]["triage_thread_members"] == [
        "N-OLD", "N-NEW"
    ]
    assert len(candidates[0]["raw_payload"]["triage_thread_evidence"]) == 2
    assert candidates[0]["raw_payload"]["triage_notice_lineage"] == [
        {
            "source_id": "N-OLD",
            "solicitation_id": "SOL-1",
            "posted_date": "2026-07-01",
            "response_deadline": None,
        },
        {
            "source_id": "N-NEW",
            "solicitation_id": "SOL-1",
            "posted_date": "2026-07-15",
            "response_deadline": None,
        },
    ]
    assert receipt["model_candidates_before_thread_consolidation"] == 2
    assert receipt["model_candidates_after_solicitation_consolidation"] == 1
    assert receipt["model_candidates"] == 1
    assert receipt["superseded_revisions"] == 1
    assert receipt["cross_post_duplicates"] == 0
    assert receipt["complete"] is True


def _cross_post_notice(
    source_id: str,
    solicitation: str,
    *,
    deadline: str = "2026-08-21",
    description: str | None = None,
) -> dict:
    return {
        "source_id": source_id,
        "title": "Data Center Switching Modernization RFI",
        "agency": "DEPARTMENT OF ENERGY / NATIONAL LABORATORY",
        "posted_date": "2026-07-21",
        "response_deadline": deadline,
        "naics_code": "541512",
        "contacts": [{
            "email": "network-rfi@example.gov",
            "contact_type": "primary",
        }],
        "raw_payload": {
            "solicitation": solicitation,
            "agency": "Department of Energy",
            "office": "National Laboratory Procurement Office",
            "type": "Sources Sought",
            "poc_email": "NETWORK-RFI@example.gov",
            "description_snippet": description or (
                "The laboratory seeks industry input for a data center "
                "switching modernization requirement, including resilient "
                "leaf-spine infrastructure, operations, and lifecycle support."
            ),
        },
    }


def test_sam_cross_posts_receive_one_judgment_and_preserve_every_identity():
    notices = [
        _cross_post_notice("N-334", "NETWORK-RFI(334-EQUIPMENT)"),
        _cross_post_notice("N-BASE", "NETWORK-RFI"),
        _cross_post_notice("N-541", "NETWORK-RFI(541-SERVICES)"),
    ]

    candidates, ruled, receipt = deterministic_prefilter(notices, TAXONOMY)

    assert [row["source_id"] for row in candidates] == ["N-BASE"]
    assert set(ruled) == {"N-334", "N-541"}
    assert all(row["screen"] == "deterministic-sam-cross-post-v1"
               for row in ruled.values())
    assert all(row["superseded_by"] == "N-BASE"
               for row in ruled.values())
    raw = candidates[0]["raw_payload"]
    assert raw["triage_cross_post_members"] == ["N-334", "N-BASE", "N-541"]
    assert raw["triage_cross_post_solicitations"] == [
        "NETWORK-RFI(334-EQUIPMENT)",
        "NETWORK-RFI",
        "NETWORK-RFI(541-SERVICES)",
    ]
    assert [(row["source_id"], row["solicitation_id"])
            for row in raw["triage_notice_lineage"]] == [
        ("N-334", "NETWORK-RFI(334-EQUIPMENT)"),
        ("N-BASE", "NETWORK-RFI"),
        ("N-541", "NETWORK-RFI(541-SERVICES)"),
    ]
    assert receipt["model_candidates_before_thread_consolidation"] == 3
    assert receipt["model_candidates_after_solicitation_consolidation"] == 3
    assert receipt["model_candidates"] == 1
    assert receipt["superseded_revisions"] == 0
    assert receipt["cross_post_duplicates"] == 2
    assert receipt["complete"] is True


def test_cross_post_lineage_includes_prior_revisions_from_each_solicitation():
    old = _cross_post_notice("N-BASE-OLD", "NETWORK-RFI")
    old["posted_date"] = "2026-07-20"
    latest = _cross_post_notice("N-BASE-NEW", "NETWORK-RFI")
    suffix = _cross_post_notice("N-334", "NETWORK-RFI(334-EQUIPMENT)")

    candidates, ruled, receipt = deterministic_prefilter(
        [old, latest, suffix], TAXONOMY)

    assert [row["source_id"] for row in candidates] == ["N-BASE-NEW"]
    assert ruled["N-BASE-OLD"]["screen"] == (
        "deterministic-solicitation-thread-v1")
    assert ruled["N-334"]["screen"] == "deterministic-sam-cross-post-v1"
    lineage = candidates[0]["raw_payload"]["triage_notice_lineage"]
    assert [(row["source_id"], row["solicitation_id"])
            for row in lineage] == [
        ("N-BASE-OLD", "NETWORK-RFI"),
        ("N-BASE-NEW", "NETWORK-RFI"),
        ("N-334", "NETWORK-RFI(334-EQUIPMENT)"),
    ]
    assert receipt["superseded_revisions"] == 1
    assert receipt["cross_post_duplicates"] == 1
    assert receipt["complete"] is True


def test_cross_post_consolidation_does_not_collapse_distinct_lots():
    lot_a = _cross_post_notice(
        "LOT-A",
        "NETWORK-RFI-LOT-A",
        description=(
            "Lot A covers data center switching modernization for the primary "
            "campus, with resilient leaf-spine infrastructure and lifecycle "
            "support limited to the production environment."
        ),
    )
    lot_b = _cross_post_notice(
        "LOT-B",
        "NETWORK-RFI-LOT-B",
        description=(
            "Lot B covers data center switching modernization for the research "
            "campus, with resilient leaf-spine infrastructure and lifecycle "
            "support limited to the laboratory environment."
        ),
    )

    candidates, ruled, receipt = deterministic_prefilter(
        [lot_a, lot_b], TAXONOMY)

    assert [row["source_id"] for row in candidates] == ["LOT-A", "LOT-B"]
    assert ruled == {}
    assert receipt["cross_post_duplicates"] == 0


def test_cross_post_consolidation_requires_matching_deadline_and_poc():
    baseline = _cross_post_notice("BASE", "NETWORK-RFI")
    later = _cross_post_notice(
        "LATER", "NETWORK-RFI-LATER", deadline="2026-08-26")
    other_poc = _cross_post_notice("OTHER-POC", "NETWORK-RFI-OTHER-POC")
    other_poc["raw_payload"]["poc_email"] = "other-poc@example.gov"
    other_poc["contacts"][0]["email"] = "other-poc@example.gov"

    candidates, ruled, receipt = deterministic_prefilter(
        [baseline, later, other_poc], TAXONOMY)

    assert [row["source_id"] for row in candidates] == [
        "BASE", "LATER", "OTHER-POC"]
    assert ruled == {}
    assert receipt["cross_post_duplicates"] == 0


def test_same_solicitation_number_at_different_agencies_is_not_an_amendment():
    energy = _cross_post_notice("DOE-RFI", "RFI-01")
    state = _cross_post_notice("STATE-RFI", "RFI-01")
    state["agency"] = "DEPARTMENT OF STATE"
    state["raw_payload"]["agency"] = "Department of State"
    state["raw_payload"]["office"] = "Office of Acquisition Management"

    candidates, ruled, receipt = deterministic_prefilter(
        [energy, state], TAXONOMY)

    assert [row["source_id"] for row in candidates] == ["DOE-RFI", "STATE-RFI"]
    assert ruled == {}
    assert receipt["superseded_revisions"] == 0
    assert receipt["cross_post_duplicates"] == 0
