"""Partnering profile: human-attested config — validation on load, unset vs
attested-empty semantics, never a guess. Offline."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.partnering.profile import (  # noqa: E402
    ATTESTABLE_FIELDS, PartneringProfile, ProfileError, load_partnering_profile,
)


def _write(tmp_path, monkeypatch, body) -> None:
    monkeypatch.setenv("LILA_REVIEW_DIR", str(tmp_path))
    (tmp_path / "testco.partnering.json").write_text(json.dumps(body))


def test_missing_file_means_everything_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_REVIEW_DIR", str(tmp_path))
    profile, unset, warnings = load_partnering_profile("testco")
    assert profile == PartneringProfile()
    assert unset == list(ATTESTABLE_FIELDS)
    assert warnings == []


def test_full_profile_parses_with_nothing_unset(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "client_name": "Testco",
        "size_status_by_naics": {"334310": "small"},
        "certifications": ["SDVOSB"],
        "vehicles_held": ["SEWP VI (NASA)"],
        "award_band": {"floor_usd": 50_000, "ceiling_usd": 1_500_000},
    })
    profile, unset, _w = load_partnering_profile("testco")
    assert unset == []
    assert profile.size_status_by_naics["334310"] == "small"
    assert profile.award_band.ceiling_usd == 1_500_000


def test_attested_empty_is_not_unset(tmp_path, monkeypatch):
    """'certifications': [] is an ATTESTATION (none held), not a missing field
    — the eligibility blocker may run on it. Omitted keys stay unset."""
    _write(tmp_path, monkeypatch, {"certifications": [], "vehicles_held": []})
    _profile, unset, _w = load_partnering_profile("testco")
    assert "certifications" not in unset
    assert "vehicles_held" not in unset
    assert "size_status_by_naics" in unset
    assert "award_band" in unset


def test_underscore_keys_are_comments(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"_readme": ["hi"], "certifications": []})
    _profile, unset, _w = load_partnering_profile("testco")
    assert "certifications" not in unset


def test_invalid_values_reject_loudly_never_coerce(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"certifications": ["8a"]})  # typo of 8(a)
    with pytest.raises(ProfileError, match="unknown certification"):
        load_partnering_profile("testco")

    _write(tmp_path, monkeypatch, {"size_status_by_naics": {"334310": "smallish"}})
    with pytest.raises(ProfileError, match="size_status_by_naics"):
        load_partnering_profile("testco")

    _write(tmp_path, monkeypatch, {"award_band": {"floor_usd": 10, "ceiling_usd": 5}})
    with pytest.raises(ProfileError, match="ceiling_usd is below floor_usd"):
        load_partnering_profile("testco")

    (tmp_path / "testco.partnering.json").write_text("{not json")
    with pytest.raises(ProfileError, match="invalid JSON"):
        load_partnering_profile("testco")


def test_unknown_vehicle_kept_with_warning(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"vehicles_held": ["Mystery GWAC 9"]})
    profile, _unset, warnings = load_partnering_profile("testco")
    assert profile.vehicles_held == ["Mystery GWAC 9"]   # attestation wins
    assert any("Mystery GWAC 9" in w for w in warnings)


def test_bad_naics_key_warns_but_loads(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"size_status_by_naics": {"33431": "small"}})
    profile, _unset, warnings = load_partnering_profile("testco")
    assert profile.size_status_by_naics == {"33431": "small"}
    assert any("6-digit" in w for w in warnings)
