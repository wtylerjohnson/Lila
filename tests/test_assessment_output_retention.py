"""2026-09-08: assessments survive lead HOLD and failed replacement builds."""
import json
from types import SimpleNamespace

import pytest

from agents.golden_press import product_bundle as bundle
from agents.golden_press.external_product_projection import (
    build_external_product_document,
)
from agents.golden_press.external_product_render import render_external_product
from agents.golden_press.records import GoldenRecord
from tests.test_external_product_projection import _graph, _mark, _market_map, _pack


def test_held_assessment_remains_visible_without_becoming_ranked_lead(monkeypatch):
    graph = _graph()
    held = {**graph["records"][0], "record_id": "HELD-2", "lane": "L1_notice",
            "title": "Network renewal requiring review", "requirement_family": "family-2"}
    graph["records"].append(held)
    graph["held_opportunities"] = [{"record_id": "HELD-2",
                                   "qualification_state": "needs_eligible_route",
                                   "qualification_reason": "Find an eligible reseller."}]
    receipt = {"status": "complete", "tiers": {"HOLD": 1}, "receipt": {
        "client_name": "Acme",
        "parents": [{"assessment_id": "parent-2", "notice_id": "HELD-2"}],
        "leads": [{"parent_assessment_id": "parent-2", "lead_tier": "HOLD",
                   "next_action": {"verb": "identify_route"}}]}}
    doc = build_external_product_document(
        market_map=_market_map(), graph_payload=graph, evidence_pack=_pack(),
        profile={}, client_name="Acme", slug="acme", as_of="2026-09-08", leadgen=receipt)
    assert len(doc.slots[4].records) == 2
    assert doc.slots[4].coverage["qualified"] == 0  # serialized graph has no current source authority
    assert doc.slots[4].records[1]["lead_status"] == "Current lead withheld; saved history retained"
    assert doc.slots[4].records[1]["saved_lead_rows"][0]["lead_tier"] == "HOLD"
    assert len(doc.slots[0].records) == 0  # qualification alone is not seller readiness
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, "client_logo", lambda _: _mark())
    monkeypatch.setattr(report_assets, "gtm_logo", _mark)
    _, html = render_external_product(doc)
    assert "Network renewal requiring review" in html
    assert "identify_route" in html
    assert "Opportunity assessments" in html


def test_cross_client_leads_cannot_attach_to_assessments():
    with pytest.raises(ValueError, match="different client"):
        build_external_product_document(
            market_map=_market_map(), graph_payload=_graph(), evidence_pack=_pack(),
            profile={}, client_name="Acme", slug="acme", as_of="2026-09-08",
            leadgen={"receipt": {"client_name": "Another company"}})


@pytest.mark.parametrize("records", [[], [{"record_id": "one", "lane": "L1_notice"}]])
def test_replacement_cannot_silently_drop_prior_record_or_detail(tmp_path, monkeypatch, records):
    (tmp_path / "evidence_pack.json").write_text(json.dumps({"records": [
        {"record_id": "one", "description": "Full published requirement"}]}))
    monkeypatch.setattr(bundle, "product_release_state", lambda *a, **k: {
        "releasable": True, "manifest_path": str(tmp_path / "manifest.json")})
    pack = SimpleNamespace(records=[GoldenRecord(**r) for r in records])
    with pytest.raises(bundle.ProductReleaseBlocked, match="prior complete release preserved"):
        bundle._require_output_retention(tmp_path, "acme", pack)


def test_leadgen_companion_uses_real_assess_and_preserves_source_dates(tmp_path, monkeypatch):
    from agents.golden_press import product_leadgen
    from tests.test_leadgen_from_assess import _run
    run = _run()
    monkeypatch.setattr(product_leadgen, "export_current_assess_run",
                        lambda *a, **k: {"run": run.model_dump(mode="json")})
    result = product_leadgen.build_leadgen_companion(run.client_name, tmp_path)
    assert result["status"] == "complete"
    assert result["assessment_count"] == len(run.live.records) + len(run.horizon.items) + len(run.partners.items)
    assert result["receipt"]["assess_run_id"] == run.run_id
    assert "Lead" in result["html"]


def test_full_assess_population_survives_sparse_pack_and_missing_store():
    from agents.golden_press.product_leadgen import restore_assessment_population
    from agents.golden_press.records import EvidencePack
    from tests.test_leadgen_from_assess import _live_research, _run
    run = _run(live=[_live_research()])
    pack = EvidencePack(client_name=run.client_name, generated_at="2026-09-08")
    result = restore_assessment_population(pack, {"assessment": {"run": run.model_dump(mode="json")}})
    assert len(result.records) == 1
    assert result.records[0].record_id == "N-res"
    assert result.records[0].source_fields["classification"] == run.live.records[0].classification.value
    assert pack.records == []


def test_store_recovery_preserves_existing_facts_and_original_source_date(tmp_path):
    from agents.golden_press.product_leadgen import restore_assessment_population
    from agents.golden_press.records import EvidencePack
    from tools.notice_store import connect
    conn = connect(tmp_path / "notices.db")
    conn.execute("INSERT INTO notices (notice_id, title, description_prefix, poc_name, poc_email, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("n1", "New title", "Full notice requirement", "Sam Smith", "sam@example.gov", "2026-07-29", "2026-07-29"))
    pack = EvidencePack(client_name="Acme", generated_at="2026-09-08", records=[
        GoldenRecord(record_id="n1", lane="L1_notice", title="Existing title", retrieved_at="2026-09-08")])
    result = restore_assessment_population(pack, {}, conn=conn)
    conn.close()
    row = result.records[0]
    assert row.title == "Existing title"
    assert row.description == "Full notice requirement"
    assert row.contact_name == "Sam Smith"
    assert row.retrieved_at == "2026-07-29"
    assert row.source_fields["retained_store_detail"]["notice_id"] == "n1"
