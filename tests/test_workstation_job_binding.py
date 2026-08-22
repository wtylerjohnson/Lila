"""Child-process scope binding for workstation-owned jobs."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import agents.review as review
from agents.decisions.schemas import IntakeStrategy


def _approved_dhs_packet(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
    strategy = IntakeStrategy(
        client_name="Testco",
        pursuit_strategy="Protect the exact job scope.",
        confidence=0.8,
        review_gate="approved",
    )
    packet = review.ReviewPacket(
        client_name="Testco",
        status=review.ReviewStatus.APPROVED,
        strategy=strategy,
        search_scope={"agencies": ["DHS"]},
    )
    (tmp_path / "testco.review.json").write_text(
        packet.model_dump_json(indent=2), encoding="utf-8")
    return packet


def _binding_error_type():
    error_type = getattr(review, "WorkstationBindingError", None)
    assert error_type is not None, (
        "agents.review must expose WorkstationBindingError for child fail-closed "
        "scope checks")
    return error_type


def test_expected_child_binding_is_enforced_by_packet_and_scope_owners(
        tmp_path, monkeypatch):
    _approved_dhs_packet(tmp_path, monkeypatch)
    error_type = _binding_error_type()
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", "Testco")
    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "agency_dhs")

    assert review.load_packet("Testco").client_name == "Testco"
    assert review.gate_designator("Testco") == "agency_dhs"

    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "all")
    with pytest.raises(error_type, match="workstation|scope|binding"):
        review.load_packet("Testco")
    with pytest.raises(error_type, match="workstation|scope|binding"):
        review.gate_designator("Testco")

    monkeypatch.setenv("LILA_EXPECT_WORKSTATION_ID", "agency_dhs")
    monkeypatch.setenv("LILA_EXPECT_CLIENT_NAME", "Other Client")
    with pytest.raises(error_type, match="client|binding"):
        review.load_packet("Testco")


def test_run_searches_never_swallows_expected_workstation_binding_mismatch(
        monkeypatch):
    """The mismatch must stop before ThreadPoolExecutor reaches any source."""
    import concurrent.futures
    import run_searches
    import tools.capability as capability

    error_type = _binding_error_type()
    strategy = SimpleNamespace(
        inferred_naics=[], keywords=[], searches=[], set_aside_angles=[],
        pursuit_strategy="",
    )
    profile = SimpleNamespace(
        capability_terms=SimpleNamespace(core=[], adjacent=[]),
        named_competitors_and_incumbents=[], mission_components=[],
    )
    run_packet = SimpleNamespace(
        strategy=strategy,
        is_approved=True,
        revision_count=3,
        search_scope={"agencies": ["DHS"]},
    )
    monkeypatch.setattr(capability, "require_profile", lambda _client: profile)

    calls = 0

    def snapshot(_client, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(packet=run_packet, sha256="a" * 64)
        raise error_type("expected workstation binding changed")

    monkeypatch.setattr(run_searches, "load_packet_snapshot", snapshot)

    def forbidden_executor(*_args, **_kwargs):
        raise AssertionError("metered source fan-out reached after binding mismatch")

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", forbidden_executor)
    monkeypatch.setattr(sys, "argv", ["run_searches.py", "--client", "Testco"])
    with pytest.raises(error_type, match="binding"):
        run_searches.main()
