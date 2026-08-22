"""Named engagement-scope presets: expansion, precedence, and provenance."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.capability import load_profile
from tools.relevance.calibrate import calibrate_client
from tools.relevance.engine import score_record
from tools.relevance.scope import (
    TOPTIER_CODE_DEPARTMENTS,
    EngagementScope,
    in_scope,
    load_engagement_scope,
    resolve_scope_preset,
)
from tools.relevance.taxonomy import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]
PRESETS_PATH = ROOT / "data" / "reference" / "scope_presets.json"
MARK43_SCOPE_PATH = ROOT / "clients" / "mark43" / "engagement_scope.json"
RIVERBED_SCOPE_PATH = ROOT / "clients" / "riverbed" / "engagement_scope.json"

CIVILIAN_CODES = [
    "012", "013", "014", "015", "019", "020", "024", "028",
    "031", "036", "047", "049", "068", "069", "070", "072",
    "073", "075", "080", "086", "089", "091", "1601",
]
CIVILIAN_NAMES = [
    "Department of Agriculture",
    "Department of Commerce",
    "Department of the Interior",
    "Department of Justice",
    "Department of State",
    "Department of the Treasury",
    "Office of Personnel Management",
    "Social Security Administration",
    "Nuclear Regulatory Commission",
    "Department of Veterans Affairs",
    "General Services Administration",
    "National Science Foundation",
    "Environmental Protection Agency",
    "Department of Transportation",
    "Department of Homeland Security",
    "Agency for International Development",
    "Small Business Administration",
    "Department of Health and Human Services",
    "National Aeronautics and Space Administration",
    "Department of Housing and Urban Development",
    "Department of Energy",
    "Department of Education",
    "Department of Labor",
]


def _reference():
    return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))


def test_reference_v1_expands_exactly_and_comments_are_non_authoritative():
    raw = _reference()
    assert raw["version"] == 1
    assert set(raw["presets"]) == {"civilian", "defense", "all_federal"}

    entries = raw["presets"]["civilian"]["codes"]
    assert [entry["code"] for entry in entries] == CIVILIAN_CODES
    assert [entry["_comment"] for entry in entries] == CIVILIAN_NAMES
    assert len(set(CIVILIAN_CODES)) == 23
    assert "097" not in CIVILIAN_CODES
    assert all(code in TOPTIER_CODE_DEPARTMENTS
               for code in CIVILIAN_CODES)

    civilian = resolve_scope_preset("civilian")
    assert list(civilian.codes) == CIVILIAN_CODES
    assert list(civilian.departments) == [
        TOPTIER_CODE_DEPARTMENTS[code] for code in CIVILIAN_CODES]
    assert civilian.basis == "preset:civilian@v1"

    defense_entries = raw["presets"]["defense"]["codes"]
    assert defense_entries == [
        {"code": "097", "_comment": "Department of Defense"}]
    defense = resolve_scope_preset("defense")
    assert defense.codes == ("097",)
    assert defense.departments == ("DoD",)
    assert not defense.unbounded

    all_federal = resolve_scope_preset("all_federal")
    assert all_federal.unbounded
    assert all_federal.codes == ()
    assert all_federal.departments == ()


def test_preset_add_then_remove_and_remove_wins():
    scope = EngagementScope(
        preset="civilian", add=["097"], remove=["070"])
    assert "DoD" in scope.resolved_departments
    assert "DHS" not in scope.resolved_departments
    assert scope.resolved_basis == "preset:civilian@v1 (+097 -070)"
    assert scope.model_dump() == {
        "departments": [], "subtiers": [], "aliases": {},
        "preset": "civilian", "add": ["097"], "remove": ["070"],
    }
    assert in_scope({"agency": "Department of Defense"}, scope) == (
        True, "preset:civilian@v1 (+097 -070) · in-scope:DoD")
    assert in_scope({"agency": "Department of Homeland Security"}, scope) == (
        False, "preset:civilian@v1 (+097 -070) · off-scope:DHS")

    remove_wins = EngagementScope(
        preset="civilian", add=["097"], remove=["097"])
    assert "DoD" not in remove_wins.resolved_departments
    assert in_scope({"agency": "Department of the Army"}, remove_wins)[0] is False


def test_defense_covers_subtiers_and_all_federal_has_no_boundary():
    defense = EngagementScope(preset="defense")
    assert in_scope({"agency": "Department of the Army"}, defense) == (
        True, "preset:defense@v1 · in-scope:DoD")
    assert in_scope({"agency": "National Science Foundation"}, defense) == (
        False, "preset:defense@v1 · off-scope:NSF")

    all_federal = EngagementScope(preset="all_federal")
    assert in_scope({"agency": "Mystery Federal Bureau"}, all_federal) == (
        True, "preset:all_federal@v1")


def test_all_federal_remove_is_a_deterministic_deny_edit():
    scope = EngagementScope(preset="all_federal", remove=["097"])
    assert in_scope({"agency": "Department of the Navy"}, scope) == (
        False, "preset:all_federal@v1 (-097) · off-scope:DoD")
    assert in_scope({"agency": "Department of Commerce"}, scope) == (
        True, "preset:all_federal@v1 (-097) · in-scope:DOC")

    add_is_provenance_only = EngagementScope(
        preset="all_federal", add=["097"])
    assert add_is_provenance_only.resolved_codes == ()
    assert add_is_provenance_only.resolved_departments == ()
    assert add_is_provenance_only.resolved_basis == (
        "preset:all_federal@v1 (+097)")
    assert in_scope({"agency": "Department of Commerce"},
                    add_is_provenance_only) == (
        True, "preset:all_federal@v1 (+097)")


def test_existing_explicit_config_identity_is_byte_exact():
    old_config = {
        "departments": ["DHS"],
        "subtiers": ["U.S. Citizenship and Immigration Services"],
        "aliases": {"Dept. of Homeland Sec.": "DHS"},
    }
    scope = EngagementScope(**old_config)
    assert scope.model_dump() == old_config
    assert scope.model_dump_json() == (
        '{"departments":["DHS"],"subtiers":'
        '["U.S. Citizenship and Immigration Services"],"aliases":'
        '{"Dept. of Homeland Sec.":"DHS"}}')
    assert in_scope({"agency": "Department of Defense"}, scope) == (
        False, "off-scope:DoD")
    assert in_scope({"agency": "Mystery Bureau"}, scope) == (
        True, "unresolved")
    # New preset code/name coverage must not reinterpret legacy records.
    assert in_scope({"toptier_code": "097"}, scope) == (
        True, "unresolved")
    assert in_scope({"agency": "National Science Foundation"}, scope) == (
        True, "unresolved")
    legacy_dol_id = {
        "url": "https://www.usaspending.gov/award/CONT_AWD_X_1601_Y"}
    assert in_scope(legacy_dol_id, scope) == (True, "unresolved")


def test_named_presets_alone_gain_complete_code_and_cfo_name_resolution():
    civilian = EngagementScope(preset="civilian")
    defense = EngagementScope(preset="defense")
    assert in_scope({"toptier_code": "097"}, civilian) == (
        False, "preset:civilian@v1 · off-scope:DoD")
    assert in_scope({"toptier_code": "097"}, defense) == (
        True, "preset:defense@v1 · in-scope:DoD")
    assert in_scope({"agency": "National Science Foundation"}, defense) == (
        False, "preset:defense@v1 · off-scope:NSF")
    assert in_scope({"agency": "NSF"}, defense) == (
        False, "preset:defense@v1 · off-scope:NSF")
    assert in_scope(
        {"agency": "Department of Defense", "toptier_code": "070"},
        defense) == (False, "preset:defense@v1 · off-scope:DHS")
    preset_dol_id = {
        "url": "https://www.usaspending.gov/award/CONT_AWD_X_1601_Y"}
    assert in_scope(preset_dol_id, civilian) == (
        True, "preset:civilian@v1 · in-scope:DOL")


@pytest.mark.parametrize("bad_name", ["civillian", "civil", "", "CIVILIAN"])
def test_unknown_preset_fails_loudly_and_never_guesses(bad_name):
    with pytest.raises(ValidationError, match="unknown engagement_scope preset"):
        EngagementScope(preset=bad_name)


def test_invalid_code_edits_and_ambiguous_config_fail_loudly():
    with pytest.raises(ValidationError, match="unknown code"):
        EngagementScope(preset="civilian", add=["999"])
    with pytest.raises(ValidationError, match="require a named preset"):
        EngagementScope(add=["097"])
    with pytest.raises(ValidationError, match="cannot be mixed"):
        EngagementScope(preset="civilian", departments=["DHS"])
    with pytest.raises(ValidationError, match="cannot be mixed"):
        EngagementScope(preset="civilian", aliases={"Defense": "DHS"})


@pytest.mark.parametrize("bad_version", [True, 1.0, "1", 2])
def test_reference_version_must_be_exact_integer_v1(
        tmp_path, monkeypatch, bad_version):
    import tools.relevance.scope as scope_module

    raw = _reference()
    raw["version"] = bad_version
    path = tmp_path / "scope_presets.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(scope_module, "_SCOPE_PRESETS_PATH", str(path))
    with pytest.raises(ValueError, match="unsupported scope preset version"):
        resolve_scope_preset("civilian")


def test_calibration_rows_and_off_scope_verdict_carry_version_stamp(
        monkeypatch):
    import tools.relevance.calibrate as calibrate

    taxonomy = load_taxonomy("Insignary")
    scope = EngagementScope(preset="civilian")
    monkeypatch.setattr(calibrate, "load_taxonomy", lambda _client: taxonomy)
    monkeypatch.setattr(
        "tools.relevance.scope.load_engagement_scope",
        lambda _client: scope)
    sweep = {
        "client": "Scope Stamp Test",
        "results": {
            "sam.gov": [{
                "source_id": "DOD-1",
                "title": "Army software composition analysis",
                "agency": "Department of the Army",
                "description": "Binary SCA and software bill of materials",
            }],
        },
    }
    result = calibrate_client("Scope Stamp Test", sweep)
    assert result["scope_basis"] == "preset:civilian@v1"
    assert len(result["off_scope_rows"]) == 1
    row = result["off_scope_rows"][0]
    assert row["scope_basis"] == "preset:civilian@v1 · off-scope:DoD"

    verdict = score_record(
        sweep["results"]["sam.gov"][0], taxonomy,
        engagement_scope=scope)
    assert verdict.off_scope
    assert verdict.scope_basis == "preset:civilian@v1 · off-scope:DoD"


def test_riverbed_scope_is_standalone_and_one_line_flippable():
    raw_text = RIVERBED_SCOPE_PATH.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    assert "APPROVED 2026-07-19" in raw["_comment"]
    assert "DHS included" in raw["_comment"]
    assert "Department of Defense remains excluded" in raw["_comment"]
    assert raw["preset"] == "civilian"
    assert raw["remove"] == []
    assert '"070"' not in raw_text

    scope = load_engagement_scope("Riverbed")
    assert scope is not None
    assert scope.resolved_basis == "preset:civilian@v1"
    assert in_scope({"agency": "Department of Homeland Security"}, scope)[0] \
        is True
    assert in_scope({"agency": "Department of Commerce"}, scope)[0] is True
    assert in_scope({"agency": "Department of the Army"}, scope)[0] is False
    # The scope file stays standalone: sibling profile/taxonomy identity does
    # not embed or override the approved preset decision.
    profile = load_profile("Riverbed")
    taxonomy = load_taxonomy("Riverbed")
    if profile is not None:
        assert profile.is_populated()
        assert profile.client_name == "Riverbed"
    if taxonomy is not None:
        assert taxonomy.version >= 1
        assert taxonomy.engagement_scope is None   # scope stays standalone


def test_mark43_scope_is_explicitly_all_federal():
    assert json.loads(MARK43_SCOPE_PATH.read_text(encoding="utf-8")) == {
        "preset": "all_federal",
    }
    scope = load_engagement_scope("Mark43")
    assert scope is not None
    assert scope.resolved_basis == "preset:all_federal@v1"
    for agency in (
        "Department of Homeland Security",
        "Department of Commerce",
        "Department of the Navy",
        "Mystery Federal Bureau",
    ):
        assert in_scope({"agency": agency}, scope) == (
            True, "preset:all_federal@v1")
