"""Renderer (Chunk 5) tests: structure, self-containment, determinism, safety.

The known-valid golden documents from the reference-contract fixtures are the
render inputs, so these tests exercise the real schema rather than a bespoke
stand-in.  Section-content coverage uses enriched, re-validated variants.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.candidate_review_v1.contracts import (
    SECTION_ORDER,
    CandidateReviewDocument,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    SourceIdentity,
    SourceTier,
    VehicleAccessPosture,
    VehicleIdentity,
    VehicleRelationship,
    VehicleRelationshipKind,
    VehicleSignal,
    VehicleSignalKind,
    VehicleSignalStatus,
)
from agents.candidate_review_v1.renderer import (
    _esc,
    render_candidate_review,
)

_REF_PATH = Path(__file__).parent / "test_candidate_review_v1_reference_contract.py"


def _load_reference_module():
    spec = importlib.util.spec_from_file_location("_crv1_ref", _REF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REF = _load_reference_module()


def _documents() -> list[CandidateReviewDocument]:
    return [_REF._golden_document(report) for report in _REF._reports()]


def _render_all() -> list[str]:
    return [render_candidate_review(doc) for doc in _documents()]


# --------------------------------------------------------------------------- #

def test_output_is_one_standalone_html_document():
    for html in _render_all():
        assert html.startswith("<!doctype html>")
        assert "<html" in html and "</html>" in html
        assert 'id="signal-board"' in html
        assert "data-editor-ui" in html


def test_all_eight_sections_render_in_locked_order():
    for html in _render_all():
        positions = []
        for spec in SECTION_ORDER:
            marker = f'id="section-{spec.section_id}"'
            assert marker in html, f"missing section {spec.section_id}"
            positions.append(html.index(marker))
            assert spec.heading in html, f"missing heading {spec.heading!r}"
        assert positions == sorted(positions), "sections are out of locked order"


def test_faces_section_and_forbidden_labels_never_appear():
    banned = [
        "Priority federal routes",
        "account ownership",
        "Agency targets and contacts",
        "win probability",
        "pipeline value",
        "Generate Pursuit Dossier",
        "Pursuit Dossier",
        "—",  # em dash
    ]
    for html in _render_all():
        for phrase in banned:
            assert phrase not in html, f"forbidden content present: {phrase!r}"
        # no faces section id / heading
        assert 'id="section-leadership"' not in html
        assert 'id="section-accounts-faces"' not in html


def test_required_controls_and_single_refresh_action():
    for html in _render_all():
        for act in ("toggle-edit", "save-draft", "reset", "download-html",
                    "print", "undo-soft-delete"):
            assert f'data-action="{act}"' in html, f"missing control {act}"
        assert html.count('data-workflow-action="refresh-analyst-layer"') == 1
        # no forbidden legacy controls
        for token in ("re-screen", "re-search", "rerun", "rescan",
                      "promote-pipeline", "download-dossier"):
            assert f'data-action="{token}"' not in html


def test_output_is_self_contained_no_external_requests():
    for html in _render_all():
        assert "<script src" not in html
        assert '<link rel="stylesheet"' not in html
        assert "@import" not in html
        assert "fonts.googleapis" not in html and "fonts.gstatic" not in html
        # only inline style/script blocks
        assert "<style>" in html and "<script>" in html


def test_render_is_deterministic():
    for doc in _documents():
        assert render_candidate_review(doc) == render_candidate_review(doc)


def test_edit_ids_and_logo_slots_are_unique():
    for html in _render_all():
        edit_ids = re.findall(r'data-edit-id="([^"]+)"', html)
        assert edit_ids, "no editable text slots rendered"
        assert len(edit_ids) == len(set(edit_ids)), "duplicate data-edit-id"
        slots = re.findall(r'data-logo-slot-id="([^"]+)"', html)
        assert len(slots) == len(set(slots)), "duplicate data-logo-slot-id"


def test_ticker_scoreboard_and_root_present():
    for html in _render_all():
        assert "sb-news-set" in html            # report ticker
        assert "cr-scoreboard" in html          # KPI scoreboard
        assert "cr-ticker" in html


def test_no_client_bleed_between_reports():
    reports = _REF._reports()
    names = [r["client_display_name"] for r in reports]
    documents = _documents()
    for index, (doc, html) in enumerate(zip(documents, [render_candidate_review(d) for d in documents])):
        assert doc.client_name in html
        for other_index, other_name in enumerate(names):
            if other_index != index and other_name != doc.client_name:
                assert other_name not in html, f"client bleed: {other_name}"


def test_escaping_neutralizes_injected_markup():
    assert "&lt;script&gt;" in _esc("<script>alert(1)</script>")
    # inject a hostile heading through a re-validated document
    base = _documents()[0]
    ev_id = base.evidence[0].evidence_id
    data = base.model_dump(mode="python")
    data["market_signals"] = [{
        "block_id": "inject-1",
        "client_id": base.binding.client_id,
        "heading": "<script>alert(1)</script> injection probe",
        "records_show": "Records show a benign probe string only.",
        "may_suggest": "It may indicate nothing beyond the escaping test.",
        "validate_next": "Validate that rendered markup is neutralized.",
        "evidence_ids": [ev_id],
    }]
    enriched = CandidateReviewDocument.model_validate(data)
    html = render_candidate_review(enriched)
    assert "<script>alert(1)</script> injection probe" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; injection probe" in html


def test_content_sections_render_when_populated():
    base = _documents()[0]
    ev_id = base.evidence[0].evidence_id
    data = base.model_dump(mode="python")
    data["pattern_claims"] = [{
        "block_id": "pat-1", "client_id": base.binding.client_id,
        "heading": "Pattern claim heading",
        "records_show": "Records show a bounded pattern.",
        "may_suggest": "It may indicate a forming corridor.",
        "validate_next": "Validate the next official record.",
        "evidence_ids": [ev_id],
    }]
    data["market_signals"] = [{
        "block_id": "mkt-1", "client_id": base.binding.client_id,
        "heading": "Market signal heading",
        "records_show": "Records show adjacent funded demand.",
        "may_suggest": "It may indicate a buying pattern.",
        "validate_next": "Validate the buyer intent.",
        "evidence_ids": [ev_id],
    }]
    data["past_awards"] = [{
        "block_id": "pa-1", "client_id": base.binding.client_id,
        "heading": "Past award heading",
        "records_show": "Records show a prior award.",
        "may_suggest": "It may indicate an incumbent.",
        "validate_next": "Validate the period of performance.",
        "evidence_ids": [ev_id],
    }]
    data["search_concepts"] = {
        "client_id": base.binding.client_id,
        "keywords": ["cloud modernization", "data platform"],
        "naics_codes": ["541511"], "psc_codes": ["D307"], "evidence_ids": [],
    }
    data["execution_framework"] = {
        "client_id": base.binding.client_id,
        "assess": "Assess the evidenced corridors.",
        "target": "Target the buying offices.",
        "execute": "Execute the outreach plan.",
        "evidence_ids": [],
    }
    enriched = CandidateReviewDocument.model_validate(data)
    html = render_candidate_review(enriched)
    for token in ("Pattern claim heading", "Market signal heading",
                  "Past award heading", "cloud modernization", "541511",
                  "Assess the evidenced corridors.", "Target the buying offices."):
        assert token in html, f"missing rendered content: {token!r}"


def test_vehicle_only_idv_renders_without_inventing_a_program_name():
    base = _documents()[0]
    excerpt = "Official vehicle record identifies parent IDV IDV-RENDER-1."
    evidence = EvidenceRecord(
        evidence_id="E-RENDER-VEHICLE",
        client_id=base.binding.client_id,
        run_id=base.binding.run_id,
        scope_sha256=base.binding.scope_sha256,
        source_identity=SourceIdentity(
            source_system="official-vehicle",
            record_id="IDV-RENDER-1",
        ),
        source_tier=SourceTier.PROGRAM,
        source_kind=EvidenceKind.VEHICLE,
        source_name="Official vehicle source",
        source_url="https://example.gov/vehicle/IDV-RENDER-1",
        retrieved_at=base.as_of - timedelta(minutes=10),
        title="Parent IDV record",
        excerpt=excerpt,
        official_source=True,
        primary_source=True,
        supports=(EvidenceUse.ACCESS,),
        assertion_spans=(EvidenceAssertionSpan(
            assertion=EvidenceAssertion.VEHICLE_IDENTITY,
            quote=excerpt,
        ),),
    )
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-render-1",
        agency="GSA",
        name=None,
        idv_piid="IDV-RENDER-1",
        evidence_ids=(evidence.evidence_id,),
    )
    signal = VehicleSignal(
        signal_id="signal:idv-render-1",
        client_id=base.binding.client_id,
        run_id=base.binding.run_id,
        scope_sha256=base.binding.scope_sha256,
        title="Sourced parent IDV access research",
        agency="GSA",
        kind=VehicleSignalKind.ACCESS_PATH,
        status=VehicleSignalStatus.RESEARCH,
        vehicle=identity,
        relationship=VehicleRelationship(
            relationship_id="relationship:idv-render-1",
            vehicle_id=identity.vehicle_id,
            kind=VehicleRelationshipKind.VEHICLE_ONLY,
            access_posture=VehicleAccessPosture.UNKNOWN,
            evidence_ids=(evidence.evidence_id,),
        ),
        records_show="The official record identifies only the parent IDV.",
        may_suggest="The IDV may provide an acquisition route.",
        validate_next="Confirm the official program name before adding one.",
        evidence_ids=(evidence.evidence_id,),
        last_checked_at=base.as_of,
    )
    document = CandidateReviewDocument.model_validate({
        **base.model_dump(mode="python"),
        "vehicle_signals": (signal,),
        "evidence": (*base.evidence, evidence),
    })

    html = render_candidate_review(document)

    assert "Sourced parent IDV access research" in html
    assert "IDV-RENDER-1" in html
    assert "Unnamed vehicle" not in html


def test_renderer_rejects_copied_order_signal_without_exact_parent_lineage():
    base = _documents()[0]
    identity_quote = "Parent IDV IDV-RENDER-2 is the sourced vehicle identity."
    lineage_quote = "Task order TO-RENDER-2 uses parent IDV IDV-RENDER-2."
    evidence = EvidenceRecord(
        evidence_id="E-RENDER-ORDER",
        client_id=base.binding.client_id,
        run_id=base.binding.run_id,
        scope_sha256=base.binding.scope_sha256,
        source_identity=SourceIdentity(
            source_system="official-award",
            record_id="TO-RENDER-2",
        ),
        source_tier=SourceTier.PROGRAM,
        source_kind=EvidenceKind.AWARD,
        source_name="Official award source",
        source_url="https://example.gov/award/TO-RENDER-2",
        retrieved_at=base.as_of - timedelta(minutes=10),
        title="Task order record",
        excerpt=f"{identity_quote} {lineage_quote}",
        official_source=True,
        primary_source=True,
        supports=(EvidenceUse.ACCESS,),
        assertion_spans=(
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.VEHICLE_IDENTITY,
                quote=identity_quote,
            ),
            EvidenceAssertionSpan(
                assertion=EvidenceAssertion.PARENT_IDV_RELATIONSHIP,
                quote=lineage_quote,
            ),
        ),
    )
    identity = VehicleIdentity(
        vehicle_id="vehicle:idv-render-2",
        agency="GSA",
        name=None,
        idv_piid="IDV-RENDER-2",
        evidence_ids=(evidence.evidence_id,),
    )
    signal = VehicleSignal(
        signal_id="signal:to-render-2",
        client_id=base.binding.client_id,
        run_id=base.binding.run_id,
        scope_sha256=base.binding.scope_sha256,
        title="Verified task order lineage",
        agency="GSA",
        kind=VehicleSignalKind.TASK_ORDER_ACTIVITY,
        status=VehicleSignalStatus.RESEARCH,
        vehicle=identity,
        relationship=VehicleRelationship(
            relationship_id="relationship:to-render-2",
            vehicle_id=identity.vehicle_id,
            kind=VehicleRelationshipKind.TASK_ORDER,
            access_posture=VehicleAccessPosture.UNKNOWN,
            order_identity=evidence.source_identity,
            evidence_ids=(evidence.evidence_id,),
        ),
        records_show="The official order record names the exact parent IDV.",
        may_suggest="The parent IDV may be a relevant acquisition route.",
        validate_next="Confirm holder access and current ordering status.",
        evidence_ids=(evidence.evidence_id,),
        last_checked_at=base.as_of,
    )
    document = CandidateReviewDocument.model_validate({
        **base.model_dump(mode="python"),
        "vehicle_signals": (signal,),
        "evidence": (*base.evidence, evidence),
    })
    assert "Verified task order lineage" in render_candidate_review(document)

    identity_only = evidence.model_copy(update={
        "assertion_spans": (evidence.assertion_spans[0],),
    })
    unsafe_copy = document.model_copy(update={
        "evidence": tuple(
            identity_only if row.evidence_id == evidence.evidence_id else row
            for row in document.evidence
        ),
    })
    with pytest.raises(ValidationError, match="parent-IDV assertion"):
        render_candidate_review(unsafe_copy)


def test_watch_record_renders_a_section3_watch_row():
    # 2026-07-24 Pass C2: the typed vehicle watch record gets its own Section 3
    # row (identity, ordering/on-ramp status, milestone rail, evidence tabs).
    from datetime import timedelta as _td

    from agents.candidate_review_v1.contracts import (
        CoverageRecord, CoverageState,
    )
    from agents.candidate_review_v1.vehicle_watch import (
        VehicleLead, VehicleWatchLane,
    )
    from agents.candidate_review_v1.verification import (
        FetchedRecord, _mint_vehicle_evidence, _mint_vehicle_watch_record,
    )

    base = _documents()[0]
    binding = base.binding
    as_of = base.as_of
    lead = VehicleLead(
        lead_id="lead-polaris", client_id=binding.client_id,
        run_id=binding.run_id, scope_sha256=binding.scope_sha256,
        query_id="q-gsa-render", lane=VehicleWatchLane.GSA_PROGRAM_RECORDS,
        discovered_at=as_of - _td(hours=3), title="POLARIS",
        source_url="https://www.gsaelibrary.gsa.gov/vehicle/POLARIS",
        source_identity=SourceIdentity(
            source_system="gsa.gov", record_id="POLARIS"))
    page = FetchedRecord(
        lead_id=lead.lead_id, final_url=str(lead.source_url),
        fetched_at=as_of - _td(hours=1), title="Polaris GWAC",
        text=("Vehicle: Polaris. Vehicle class: GWAC. "
              "Managing agency: General Services Administration. "
              "Ordering status: Ordering is active. "
              "Source data as of July 20, 2026."),
        record_sha256="d4" * 32)
    record = _mint_vehicle_evidence(binding, as_of, lead, page, {})
    watch, skip = _mint_vehicle_watch_record(binding, as_of, lead, record)
    assert skip is None and watch is not None
    coverage_row = CoverageRecord(
        client_id=binding.client_id, run_id=binding.run_id,
        scope_sha256=binding.scope_sha256, source="gsa_elibrary",
        query_family="vehicle_program", state=CoverageState.RETURNED,
        window_start=as_of.date() - _td(days=1), window_end=as_of.date(),
        attempted_at=as_of - _td(hours=2), records_returned=1,
        records_accepted=1, accepted_evidence_ids=(record.evidence_id,),
        query_manifest_id="m-render", query_id=lead.query_id)
    document = CandidateReviewDocument.model_validate({
        **base.model_dump(mode="python"),
        "vehicle_watch_records": (watch,),
        "evidence": (*base.evidence, record),
        "coverage": (*base.coverage, coverage_row),
    })
    html = render_candidate_review(document)
    assert "cr-watch" in html
    assert "Polaris" in html
    assert "General Services Administration" in html
    assert "gwac" in html
    assert "none announced" in html
    assert "2026-07-20" in html


def test_agency_seal_renders_beside_the_agency_value(tmp_path, monkeypatch):
    # 2026-07-25 house style: the editable seal slot sits beside the agency
    # value; a missing seal renders nothing (no placeholder, no filler).
    import base64

    import agents.candidate_review_v1.renderer as renderer_mod

    seal_dir = tmp_path / "seals"
    seal_dir.mkdir()
    png = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
        b"nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")
    (seal_dir / "gsa.png").write_bytes(png)
    monkeypatch.setenv("LILA_SEALS_DIR", str(seal_dir))
    monkeypatch.setattr(renderer_mod, "_SEAL_URI_MEMO", {})

    base = _documents()[0]
    html = render_candidate_review(base)
    _ = html  # base render must not fail with the seal machinery active

    assert renderer_mod._agency_seal_uri("General Services Administration") \
        .startswith("data:image/")
    assert renderer_mod._agency_seal_uri("Completely Unknown Bureau") == ""
