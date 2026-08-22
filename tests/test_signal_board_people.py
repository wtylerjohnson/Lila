"""Evidence-bound face cards and offline portrait resolution."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path

import pytest

from agents.reports import person_portraits
from agents.reports import signal_board as board
from agents.reports import signal_board_presentation as presentation
from agents.reports import board_content
from agents.reports.board_content import DecisionMaker, SignalBoardContent


def _png(color=(30, 90, 140, 255)) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _poc(**updates) -> dict:
    row = {
        "name": "Renee Leaman",
        "role": "Primary POC",
        "agency": "U.S. Marshals Service",
        "source_system": "SAM.gov",
        "source_record_id": "40764d7844654bde814b710c58922567",
        "opp": "OpenFox renewal · USMS",
        "opp_url": "https://sam.gov/opp/40764d7844654bde814b710c58922567/view",
        "deadline": "20 JUL 2026",
        "signal": "ACTIVE",
    }
    row.update(updates)
    return row


def _resolved(client: str, row: dict, root) -> dict:
    result = person_portraits.resolve_portrait(client, row, root=root)
    return dict(
        row,
        portrait=result.data_uri,
        portrait_identity_label=result.identity_label,
        portrait_identity=result.identity,
        portrait_source_url=result.portrait_source_url,
        portrait_provenance=result.provenance,
    )


def test_person_identity_is_record_bound_and_namesake_safe():
    first = presentation.person_identity_label(
        "Jordan Lee", "Department of Justice", "SAM.gov", "notice-a")
    second = presentation.person_identity_label(
        "Jordan Lee", "Department of Justice", "SAM.gov", "notice-b")

    first_key = presentation.logo_identity("Mark43", "person", first)
    second_key = presentation.logo_identity("Mark43", "person", second)

    assert first_key.startswith("person:")
    assert first_key != second_key
    with pytest.raises(ValueError, match="identity claim"):
        presentation.logo_identity("Mark43", "person", "Jordan Lee")


def test_exact_local_portrait_embeds_with_source_and_hash(tmp_path):
    raw = _png()
    asset = tmp_path / "data" / "reference" / "portraits" / "renee.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(raw)
    row = _poc(
        portrait_asset="data/reference/portraits/renee.png",
        portrait_sha256=hashlib.sha256(raw).hexdigest(),
        portrait_source_url="https://www.usmarshals.gov/official/renee",
    )

    result = person_portraits.resolve_portrait(
        "Mark43", row, root=tmp_path)

    assert result.data_uri.startswith("data:image/png;base64,")
    assert result.portrait_source_url == row["portrait_source_url"]
    assert result.provenance == "OFFICIAL PORTRAIT"
    assert result.issue == ""


def test_bad_or_ambiguous_portrait_fails_to_editable_placeholder(tmp_path):
    raw = _png()
    asset = tmp_path / "data" / "reference" / "portraits" / "renee.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(raw)
    row = _poc(
        portrait_asset="data/reference/portraits/renee.png",
        portrait_sha256="0" * 64,
        portrait_source_url="https://www.usmarshals.gov/official/renee",
    )

    result = person_portraits.resolve_portrait(
        "Mark43", row, root=tmp_path)
    rendered = board._people_band(
        [_resolved("Mark43", _poc(), tmp_path)], [], {})

    assert result.data_uri == ""
    assert "SHA256 does not match" in result.issue
    assert 'data-brand-kind="person"' in rendered
    assert "PORTRAIT NOT SOURCED" in rendered
    assert ">RL</span>" in rendered


def test_portrait_asset_symlink_is_refused(tmp_path):
    raw = _png()
    outside = tmp_path / "outside.png"
    outside.write_bytes(raw)
    directory = tmp_path / "data" / "reference" / "portraits"
    directory.mkdir(parents=True)
    (directory / "renee.png").symlink_to(outside)
    row = _poc(
        portrait_asset="data/reference/portraits/renee.png",
        portrait_sha256=hashlib.sha256(raw).hexdigest(),
        portrait_source_url="https://www.usmarshals.gov/official/renee",
    )

    result = person_portraits.resolve_portrait(
        "Mark43", row, root=tmp_path)

    assert result.data_uri == ""
    assert "symlink" in result.issue


def test_catalog_requires_exact_contact_record_not_name_match(tmp_path):
    raw = _png()
    asset = tmp_path / "data" / "reference" / "portraits" / "renee.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(raw)
    catalog = {
        "schema_version": 1,
        "portraits": [{
            "name": "Renee Leaman",
            "organization": "U.S. Marshals Service",
            "source_system": "SAM.gov",
            "source_record_id": "different-notice",
            "portrait_source_url": "https://www.usmarshals.gov/official/renee",
            "asset": "data/reference/portraits/renee.png",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }],
    }
    (asset.parent / "index.json").write_text(
        json.dumps(catalog), encoding="utf-8")

    mismatch = person_portraits.resolve_portrait(
        "Mark43", _poc(), root=tmp_path)
    exact = person_portraits.resolve_portrait(
        "Mark43",
        _poc(source_record_id="different-notice"),
        root=tmp_path,
    )

    assert mismatch.data_uri == ""
    assert mismatch.provenance == "PORTRAIT NOT SOURCED"
    assert exact.data_uri.startswith("data:image/png;base64,")


def test_published_contact_cards_show_role_and_official_provenance(tmp_path):
    row = _resolved("Mark43", _poc(), tmp_path)

    source = board._people_band([row], [], {})

    assert "PUBLISHED SOLICITATION CONTACTS" in source
    assert "CONTACTS AS LISTED IN SAM.GOV" in source
    assert "Primary POC" in source
    assert "SAM.GOV · PUBLISHED CONTACT" in source
    assert row["source_record_id"] in source
    assert 'data-brand-display="Renee Leaman"' in source


def test_decision_makers_are_separate_and_never_called_pocs(tmp_path):
    decision = DecisionMaker(
        name="Pat Example",
        title="Chief Information Officer",
        organization="Department of the Interior",
        bio_url="https://www.doi.gov/official/pat-example",
        retrieved_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        route_kind="usaspending-award",
        route_identity="CONT_AWD_TEST",
        route_label="BLM network modernization",
        relevance_rationale=(
            "The displayed BLM route sits inside the official technology "
            "portfolio this role leads."),
    ).model_dump(mode="json")
    row = _resolved("Riverbed", decision, tmp_path)

    source = board._people_band([], [row], {})

    assert "STRATEGIC LEADERSHIP OFFICES TO VALIDATE" in source
    assert "NOT SOLICITATION POCS" in source
    assert "DECISION MAKER TO VALIDATE" in source
    assert "SOLICITATION CONTACT" not in source
    assert "OFFICIAL LEADERSHIP SOURCE" in source
    assert "BLM network modernization" in source
    assert 'data-route-kind="usaspending-award"' in source
    assert 'data-route-identity="CONT_AWD_TEST"' in source

    combined = board._people_band(
        [_resolved("Riverbed", _poc(), tmp_path)], [row], {})
    assert "TWO VERIFIED ROLE CLASSES · KEPT SEPARATE" in combined
    assert "PUBLISHED SOLICITATION CONTACTS" in combined
    assert "STRATEGIC LEADERSHIP OFFICES TO VALIDATE" in combined
    assert "NOT SOLICITATION POCS" in combined
    assert "SOLICITATION CONTACT" in combined
    assert "DECISION MAKER TO VALIDATE" in combined
    assert "Pat Example" in combined
    assert combined.count('class="sb-person-group"') == 2


def test_decision_maker_schema_requires_source_and_caps_band():
    base = {
        "name": "Pat Example",
        "title": "Chief Information Officer",
        "organization": "Department of the Interior",
        "bio_url": "https://www.doi.gov/official/pat-example",
        "retrieved_at": "2026-07-20T00:00:00Z",
        "route_kind": "usaspending-award",
        "route_identity": "CONT_AWD_TEST",
        "route_label": "BLM network modernization",
        "relevance_rationale": "Official role is tied to the displayed route.",
    }
    with pytest.raises(ValueError, match="bio_url must use HTTPS"):
        DecisionMaker(**dict(base, bio_url="http://example.invalid/person"))
    with pytest.raises(ValueError, match="route_kind"):
        DecisionMaker(**dict(base, route_kind="USASpending award"))
    with pytest.raises(ValueError, match="structured record id"):
        DecisionMaker(**dict(
            base, route_identity="https://usaspending.gov/award/TEST"))
    with pytest.raises(ValueError, match="at most 4 items"):
        SignalBoardContent(
            client_name="Riverbed",
            decision_makers=[base] * 5,
        )


def test_decision_maker_route_validator_ignores_nested_evidence_and_swaps():
    from agents.reports.board_content import (
        primary_displayed_route_keys,
        validate_decision_maker_routes,
    )

    base = DecisionMaker(
        name="Pat Example",
        title="Chief Information Officer",
        organization="Department of the Interior",
        bio_url="https://www.doi.gov/official/pat-example",
        retrieved_at="2026-07-20T00:00:00Z",
        route_kind="usaspending-award",
        route_identity="CONT_AWD_PRIMARY",
        route_label="Primary displayed route",
        relevance_rationale="Official role is tied to this displayed route.",
    )
    content = {
        "best_fit": [{
            "award_generated_id": "CONT_AWD_PRIMARY",
            "machine_evidence": {
                "source_identity": "CONT_AWD_NESTED",
                "route_basis": {
                    "award_generated_id": "CONT_AWD_NESTED",
                },
            },
        }],
        "competitors": [{
            "award_generated_id": "CONT_AWD_SECONDARY",
        }],
        "teaming": [],
        "horizon": [],
        "decision_makers": [base.model_dump(mode="json")],
    }

    assert primary_displayed_route_keys(content) == {
        ("usaspending-award", "CONT_AWD_PRIMARY"),
        ("usaspending-award", "CONT_AWD_SECONDARY"),
    }
    validate_decision_maker_routes(content, expected_people=[base])

    nested = base.model_copy(update={"route_identity": "CONT_AWD_NESTED"})
    content["decision_makers"] = [nested.model_dump(mode="json")]
    with pytest.raises(ValueError, match="primary displayed card"):
        validate_decision_maker_routes(content, expected_people=[base])

    swapped = base.model_copy(update={"route_identity": "CONT_AWD_SECONDARY"})
    content["decision_makers"] = [swapped.model_dump(mode="json")]
    with pytest.raises(ValueError, match="independent client-scoped"):
        validate_decision_maker_routes(content, expected_people=[base])


def test_client_people_input_is_separate_from_generated_content(
        tmp_path, monkeypatch):
    payload = {
        "schema_version": 1,
        "decision_makers": [{
            "name": "Pat Example",
            "title": "Chief Information Officer",
            "organization": "Department of the Interior",
            "bio_url": "https://www.doi.gov/official/pat-example",
            "retrieved_at": "2026-07-20T00:00:00Z",
            "route_kind": "usaspending-award",
            "route_identity": "CONT_AWD_TEST",
            "route_label": "BLM network modernization",
            "relevance_rationale": (
                "The official role is tied to the displayed BLM route."),
        }],
    }
    client_dir = tmp_path / "clients" / "riverbed"
    client_dir.mkdir(parents=True)
    people = client_dir / "signal_board_people.json"
    people.write_text(json.dumps(payload), encoding="utf-8")
    (client_dir / "signal_board_content.json").write_text(
        json.dumps({"client_name": "Riverbed"}), encoding="utf-8")
    monkeypatch.setattr(board_content, "_ROOT", str(tmp_path))

    first = board_content.load_decision_makers("Riverbed")
    (client_dir / "signal_board_content.json").write_text(
        json.dumps({"client_name": "Riverbed", "composition_mode": "machine"}),
        encoding="utf-8",
    )
    second = board_content.load_decision_makers("Riverbed")

    assert first == second
    assert first[0].source_record_id == first[0].bio_url
    assert board_content.people_path("Riverbed").endswith(
        "clients/riverbed/signal_board_people.json")


def test_person_override_and_size_use_existing_presentation_contract(tmp_path):
    claim = presentation.person_identity_label(
        "Renee Leaman", "U.S. Marshals Service", "SAM.gov", "notice-a")
    key = presentation.logo_identity("Mark43", "person", claim)

    presentation.store_logo_override(
        "Mark43", kind="person", label=claim, raw=_png(), root=tmp_path)
    presentation.store_logo_size(
        "Mark43", kind="person", label=claim, percent=135, root=tmp_path)

    assert presentation.resolve_logo_override(
        "Mark43", kind="person", label=claim, root=tmp_path,
    ).startswith("data:image/png;base64,")
    assert presentation.resolve_logo_sizes("Mark43", root=tmp_path) == {
        key: 135,
    }
    manifest = presentation.presentation_path("Mark43", root=tmp_path)
    assert key in manifest.read_text(encoding="utf-8")


def test_mark43_reviewed_people_seed_is_route_bound_with_official_faces():
    root = Path(__file__).resolve().parents[1]
    rows = board_content.load_decision_makers("Mark43")
    resolved = [
        _resolved("Mark43", row.model_dump(mode="json"), root)
        for row in rows
    ]

    assert [row["name"] for row in resolved] == [
        "Jon P. Hickey", "Shantrell (Nikki) Collier",
    ]
    assert sum(
        row["portrait_provenance"] == "OFFICIAL PORTRAIT"
        for row in resolved
    ) == 2
    assert all(row["portrait"].startswith("data:image/") for row in resolved)
    assert {row["route_identity"] for row in resolved} == {
        "CONT_AWD_70Z02325F76100007_7008_70B03C23D00000006_7014",
        "CONT_AWD_15F06724P0000749_1549_-NONE-_-NONE-",
    }

    source = board._people_band([], resolved, {})
    assert "STRATEGIC LEADERSHIP OFFICES TO VALIDATE" in source
    assert "NOT SOLICITATION POCS" in source
    assert source.count("OFFICIAL PORTRAIT") == 2
    assert "Jon P. Hickey" in source
    assert "Shantrell (Nikki) Collier" in source
    assert "—" not in source


def test_riverbed_reviewed_people_seed_resolves_all_official_faces():
    root = Path(__file__).resolve().parents[1]
    rows = board_content.load_decision_makers("Riverbed")
    resolved = [
        _resolved("Riverbed", row.model_dump(mode="json"), root)
        for row in rows
    ]

    assert [row["name"] for row in resolved] == [
        "Jim Rolfes", "Darren Ash", "Shantrell (Nikki) Collier",
    ]
    assert all(
        row["portrait_provenance"] == "OFFICIAL PORTRAIT"
        for row in resolved
    )
    assert {row["route_identity"] for row in resolved} == {
        "CONT_AWD_140L0625F0144_1422_NNG15SC91B_8000",
        "CONT_AWD_15F06724F0001868_1549_NNG15SC91B_8000",
    }

    source = board._people_band([], resolved, {})
    assert source.count("OFFICIAL PORTRAIT") == 3
    assert source.count('data-route-kind="usaspending-award"') == 3


def test_max_people_layout_keeps_role_groups_and_all_eight_cards(tmp_path):
    pocs = [
        _resolved(
            "Mark43",
            _poc(
                name=f"Published Contact {index}",
                source_record_id=f"notice-{index}",
                opp_url=f"https://sam.gov/opp/notice-{index}/view",
            ),
            tmp_path,
        )
        for index in range(4)
    ]
    decision_makers = [
        _resolved(
            "Mark43",
            DecisionMaker(
                name=f"Decision Maker {index}",
                title="Chief Information Officer",
                organization="Federal Agency",
                bio_url=f"https://agency.gov/leadership/person-{index}",
                retrieved_at="2026-07-20T00:00:00Z",
                route_kind="usaspending-award",
                route_identity=f"CONT_AWD_{index}",
                route_label=f"Displayed award {index}",
                relevance_rationale="Official role maps to this route.",
            ).model_dump(mode="json"),
            tmp_path,
        )
        for index in range(4)
    ]

    source = board._people_band(pocs, decision_makers, {})

    assert source.count('class="sb-person-group"') == 2
    assert source.count("sb-poc-card") == 4
    assert source.count("sb-decision-maker-card") == 4
    assert source.count('class="sb-person-card') == 8
    assert "—" not in source
    template = (
        Path(__file__).resolve().parents[1]
        / "agents/reports/templates/signal_board.template.html"
    ).read_text(encoding="utf-8")
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in template
    assert ".sb-person-grid { grid-template-columns: 1fr; }" in template
