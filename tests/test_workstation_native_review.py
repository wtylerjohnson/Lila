"""Workstation-native strategy packets own their exact review family.

These tests pin the Tranche 3 strategy-storage boundary.  A workstation id is
an identity selector, never permission to rewrite the legacy packet's scope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agents.review as review
from agents.decisions.schemas import IntakeStrategy


CLIENT = "Testco"
DHS = {"agencies": ["DHS"]}
DOD = {"agencies": ["DoD"]}
ALL = {"all": True}


def _packet(*, scope: dict, marker: str,
            status: review.ReviewStatus = review.ReviewStatus.PENDING
            ) -> review.ReviewPacket:
    strategy = IntakeStrategy(
        client_name=CLIENT,
        pursuit_strategy=marker,
        keywords=[{
            "term": f"{marker} term",
            "category": "technology",
            "rationale": f"{marker} rationale",
        }],
        confidence=0.8,
        review_gate="Review the exact workstation boundary.",
    )
    return review.ReviewPacket(
        client_name=CLIENT,
        status=status,
        strategy=strategy,
        search_scope=scope,
    )


def _family_stem(workstation_id: str | None) -> str:
    return "testco" if workstation_id is None else f"testco.{workstation_id}"


def _seed_family(
    root: Path,
    *,
    workstation_id: str | None,
    scope: dict,
    marker: str,
    status: review.ReviewStatus = review.ReviewStatus.PENDING,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stem = _family_stem(workstation_id)
    packet_path = root / f"{stem}.review.json"
    packet_path.write_text(
        _packet(scope=scope, marker=marker, status=status).model_dump_json(
            indent=2),
        encoding="utf-8",
    )
    (root / f"{stem}.review.md").write_text(
        f"{marker} markdown\n", encoding="utf-8")
    (root / f"{stem}.journal.jsonl").write_text(
        json.dumps({"event": "seed", "marker": marker}) + "\n",
        encoding="utf-8",
    )
    return packet_path


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(root.iterdir())
        if path.is_file()
    }


def test_native_path_helpers_pin_exact_json_and_journal_names(tmp_path):
    assert Path(review._path(
        CLIENT, workstation_id="agency_dhs", review_dir=tmp_path
    )) == tmp_path / "testco.agency_dhs.review.json"
    assert Path(review._journal_path(
        CLIENT, workstation_id="agency_dhs", review_dir=tmp_path
    )) == tmp_path / "testco.agency_dhs.journal.jsonl"
    assert Path(review._path(
        CLIENT, workstation_id="all", review_dir=tmp_path
    )) == tmp_path / "testco.all.review.json"

    # Omission remains the legacy family during the compatibility window.
    assert Path(review._path(
        CLIENT, review_dir=tmp_path
    )) == tmp_path / "testco.review.json"
    assert Path(review._journal_path(
        CLIENT, review_dir=tmp_path
    )) == tmp_path / "testco.journal.jsonl"


def test_native_revise_decide_and_amend_touch_only_selected_family(tmp_path):
    """All strategy decisions, prose, and journal rows stay in one family."""
    _seed_family(
        tmp_path, workstation_id=None, scope=ALL, marker="legacy all",
        status=review.ReviewStatus.APPROVED)
    _seed_family(
        tmp_path, workstation_id="agency_dod", scope=DOD, marker="DoD native")
    _seed_family(
        tmp_path, workstation_id="agency_dhs", scope=DHS, marker="DHS native")

    protected_before = {
        name: body for name, body in _tree(tmp_path).items()
        if ".agency_dhs." not in name
    }

    review.revise(
        CLIENT,
        {"pursuit_strategy": "DHS revised strategy"},
        workstation_id="agency_dhs",
        review_dir=tmp_path,
    )
    review.decide(
        CLIENT,
        True,
        note="Approve DHS only",
        workstation_id="agency_dhs",
        review_dir=tmp_path,
    )
    review.amend_terms(
        CLIENT,
        keywords=[{
            "term": "continuous diagnostics",
            "category": "technology",
            "rationale": "Specific to the accepted DHS boundary.",
        }],
        workstation_id="agency_dhs",
        review_dir=tmp_path,
    )

    after = _tree(tmp_path)
    for name, body in protected_before.items():
        assert after[name] == body, f"unselected review family changed: {name}"

    selected = review.load_packet(
        CLIENT, workstation_id="agency_dhs", review_dir=tmp_path)
    assert selected.search_scope == DHS
    assert selected.status == review.ReviewStatus.APPROVED
    assert selected.reviewer_note == "Approve DHS only"
    assert selected.revision_count == 2
    assert selected.strategy.pursuit_strategy == "DHS revised strategy"
    assert [keyword.term for keyword in selected.strategy.keywords] == [
        "continuous diagnostics"
    ]

    native_markdown = (
        tmp_path / "testco.agency_dhs.review.md").read_text(encoding="utf-8")
    assert "continuous diagnostics" in native_markdown
    assert "`agency_dhs`" in native_markdown
    assert "approve.py" not in native_markdown
    rows = [
        json.loads(line)
        for line in (
            tmp_path / "testco.agency_dhs.journal.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event"] for row in rows] == [
        "seed", "gate_revision", "decision", "term_amendment"
    ]
    assert all(row["event"] != "scope_change" for row in rows)
    assert not (tmp_path / "testco.agency_dhs.review.review.md").exists()


@pytest.mark.parametrize("operation", ["load", "revise", "decide", "amend"])
def test_native_packet_scope_must_match_requested_workstation(
        tmp_path, operation):
    """A misleading native filename is corruption, not a fallback signal."""
    _seed_family(
        tmp_path,
        workstation_id="agency_dod",
        scope=DHS,
        marker="wrongly bound native",
        status=review.ReviewStatus.APPROVED,
    )
    before = _tree(tmp_path)

    with pytest.raises(review.WorkstationBindingError, match="workstation|scope"):
        if operation == "load":
            review.load_packet(
                CLIENT, workstation_id="agency_dod", review_dir=tmp_path)
        elif operation == "revise":
            review.revise(
                CLIENT,
                {"pursuit_strategy": "must not persist"},
                workstation_id="agency_dod",
                review_dir=tmp_path,
            )
        elif operation == "decide":
            review.decide(
                CLIENT,
                False,
                workstation_id="agency_dod",
                review_dir=tmp_path,
            )
        else:
            review.amend_terms(
                CLIENT,
                keywords=[],
                workstation_id="agency_dod",
                review_dir=tmp_path,
            )

    assert _tree(tmp_path) == before


def test_child_environment_prefers_exact_native_packet(tmp_path, monkeypatch):
    """A launched DHS job reads DHS-native state even while legacy is present."""
    _seed_family(
        tmp_path,
        workstation_id=None,
        scope=ALL,
        marker="conflicting legacy All packet",
        status=review.ReviewStatus.APPROVED,
    )
    _seed_family(
        tmp_path,
        workstation_id="agency_dhs",
        scope=DHS,
        marker="exact native packet",
        status=review.ReviewStatus.APPROVED,
    )
    before = _tree(tmp_path)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "agency_dhs")

    packet = review.load_packet(CLIENT, review_dir=tmp_path)

    assert packet.strategy.pursuit_strategy == "exact native packet"
    assert packet.search_scope == DHS
    assert review.gate_designator(CLIENT, review_dir=tmp_path) == "agency_dhs"
    assert _tree(tmp_path) == before


def test_child_environment_allows_only_exact_legacy_current_fallback(
        tmp_path, monkeypatch):
    """Legacy compatibility is eligible only when its embedded id matches."""
    _seed_family(
        tmp_path,
        workstation_id=None,
        scope=DHS,
        marker="legacy DHS current",
        status=review.ReviewStatus.APPROVED,
    )
    before = _tree(tmp_path)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "agency_dhs")

    implicit = review.load_packet(CLIENT, review_dir=tmp_path)
    explicit = review.load_packet(
        CLIENT, workstation_id="agency_dhs", review_dir=tmp_path)

    assert implicit.strategy.pursuit_strategy == "legacy DHS current"
    assert explicit.strategy.pursuit_strategy == "legacy DHS current"
    assert implicit.search_scope == explicit.search_scope == DHS
    assert not (tmp_path / "testco.agency_dhs.review.json").exists()
    assert _tree(tmp_path) == before


def test_forced_legacy_owner_ignores_unregistered_same_id_native_prefix(
        tmp_path, monkeypatch):
    """A registry-last crash prefix is bytes on disk, never legacy authority."""
    legacy = _seed_family(
        tmp_path, workstation_id=None, scope=ALL,
        marker="authorized legacy All",
        status=review.ReviewStatus.APPROVED)
    orphan = _seed_family(
        tmp_path, workstation_id="all", scope=ALL,
        marker="unregistered native prefix")
    orphan_before = orphan.read_bytes()
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "all")
    monkeypatch.setenv("LILA_EXPECT_NATIVE_WORKSTATION", "0")

    snapshot = review.load_packet_snapshot(
        CLIENT, workstation_id="all", review_dir=tmp_path)
    assert snapshot.path == str(legacy)
    assert snapshot.packet.strategy.pursuit_strategy == "authorized legacy All"
    assert review.load_packet(
        CLIENT, review_dir=tmp_path).strategy.pursuit_strategy == (
            "authorized legacy All")
    assert review.load_approved(
        CLIENT, review_dir=tmp_path).pursuit_strategy == (
            "authorized legacy All")
    assert review.gate_designator(CLIENT, review_dir=tmp_path) is None

    review.decide(
        CLIENT, True, workstation_id="all", review_dir=tmp_path,
        native_owner=False)

    assert review.ReviewPacket.model_validate_json(
        legacy.read_bytes()).status == review.ReviewStatus.APPROVED
    assert orphan.read_bytes() == orphan_before
    assert review.ReviewPacket.model_validate_json(
        orphan.read_bytes()).status == review.ReviewStatus.PENDING


def test_child_environment_rejects_cross_scope_legacy_fallback(
        tmp_path, monkeypatch):
    _seed_family(
        tmp_path,
        workstation_id=None,
        scope=ALL,
        marker="legacy All current",
        status=review.ReviewStatus.APPROVED,
    )
    before = _tree(tmp_path)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "agency_dhs")

    with pytest.raises(review.WorkstationBindingError, match="workstation|scope"):
        review.load_packet(CLIENT, review_dir=tmp_path)
    with pytest.raises(review.WorkstationBindingError, match="workstation|scope"):
        review.load_packet(
            CLIENT, workstation_id="agency_dhs", review_dir=tmp_path)

    assert not (tmp_path / "testco.agency_dhs.review.json").exists()
    assert _tree(tmp_path) == before


def test_invalid_native_packet_never_falls_back_to_compatible_legacy(
        tmp_path, monkeypatch):
    """Native presence commits resolution to that family and fails closed."""
    _seed_family(
        tmp_path,
        workstation_id=None,
        scope=DHS,
        marker="compatible legacy DHS",
        status=review.ReviewStatus.APPROVED,
    )
    _seed_family(
        tmp_path,
        workstation_id="agency_dhs",
        scope=DOD,
        marker="corrupt native DoD binding",
        status=review.ReviewStatus.APPROVED,
    )
    before = _tree(tmp_path)
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", CLIENT)
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "agency_dhs")

    with pytest.raises(review.WorkstationBindingError, match="workstation|scope"):
        review.load_packet(CLIENT, review_dir=tmp_path)

    assert _tree(tmp_path) == before
