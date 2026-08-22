"""Onboarding flow: validated save through the runners' own schemas.

Offline; temp client dirs via module-root monkeypatching; Flask test
client; no subprocesses. Doctrine: the UI never duplicates validation
logic; a saved client is by definition sweep-ready; validator error text
surfaces verbatim; CAS per file, never a silent clobber.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

flask = pytest.importorskip("flask")

import ui.onboarding as ob  # noqa: E402
import ui.server as srv  # noqa: E402

GOOD_PROFILE = {
    "client_name": "Testco",
    "capability_terms": {"core": ["video wall"], "adjacent": [],
                         "excluded": []},
    "named_competitors_and_incumbents": [],
    "mission_components": [],
    "naics_boundary": ["334310"],
}
GOOD_TAXONOMY = {
    "client_name": "Testco", "version": 1, "updated": "2026-07-17",
    "core": [{"term": "video wall", "mode": "stemmed"}],
    "adjacent": [], "exclude": [],
}
GOOD_SCOPE = {"preset": "all_federal"}


@pytest.fixture()
def clientroom(tmp_path, monkeypatch):
    """Every module that resolves clients/<slug> points at one tmp root."""
    import tools.capability as cap
    import tools.relevance.scope as scope_mod
    import tools.relevance.taxonomy as tax_mod
    monkeypatch.setattr(ob, "_ROOT", str(tmp_path))
    monkeypatch.setattr(cap, "ROOT", str(tmp_path))
    monkeypatch.setattr(cap, "CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setattr(tax_mod, "_ROOT", str(tmp_path))
    monkeypatch.setattr(scope_mod, "_ROOT", str(tmp_path))
    (tmp_path / "clients").mkdir()
    return tmp_path


def test_save_validates_through_runner_schemas_before_any_byte(clientroom):
    bad_profile = dict(GOOD_PROFILE, capability_terms={"core": [],
                                                       "adjacent": [],
                                                       "excluded": []})
    with pytest.raises(ob.OnboardingValidationError) as e:
        ob.save("Testco", {"profile": bad_profile, "scope": GOOD_SCOPE})
    assert "require_profile gate" in e.value.errors["profile"]
    # nothing landed: validation precedes every write, all-or-nothing
    assert not os.path.exists(ob.file_path("Testco", "profile"))
    assert not os.path.exists(ob.file_path("Testco", "scope"))


def test_saved_client_is_by_definition_sweep_ready(clientroom):
    state = ob.save("Testco", {"profile": GOOD_PROFILE,
                               "taxonomy": GOOD_TAXONOMY,
                               "scope": GOOD_SCOPE})
    assert state["sweep_ready"] is True
    assert state["files"]["profile"]["valid"]
    assert state["files"]["taxonomy"]["valid"]
    assert state["files"]["scope"]["valid"]
    assert state["scope_preset"] == "all_federal"
    from tools.capability import require_profile
    assert require_profile("Testco").client_name == "Testco"


def test_invalid_scope_surfaces_validator_text_verbatim(clientroom):
    bad = {"preset": "civilian", "departments": ["DHS"]}
    with pytest.raises(ob.OnboardingValidationError) as e:
        ob.save("Testco", {"scope": bad})
    assert "preset cannot be mixed" in e.value.errors["scope"]


def test_cas_conflict_never_silently_clobbers(clientroom):
    ob.save("Testco", {"profile": GOOD_PROFILE})
    current = ob._sha(ob.file_path("Testco", "profile"))
    with pytest.raises(ob.OnboardingConflict):
        ob.save("Testco", {"profile": GOOD_PROFILE},
                expected_shas={"profile": "not-the-sha"})
    ob.save("Testco", {"profile": dict(GOOD_PROFILE)},
            expected_shas={"profile": current})   # correct sha passes


def test_api_roundtrip_new_client_then_edit_view(clientroom):
    app = srv.app.test_client()
    r = app.post("/api/onboarding", json={
        "client_name": "Testco", "profile": GOOD_PROFILE,
        "taxonomy": GOOD_TAXONOMY, "scope": GOOD_SCOPE})
    assert r.status_code == 200
    body = r.get_json()
    assert body["sweep_ready"] is True

    view = app.get("/api/onboarding/testco").get_json()
    assert view["client_name"] == "Testco"     # resolved from stored profile
    assert view["files"]["profile"]["raw"]["client_name"] == "Testco"
    assert view["files"]["taxonomy"]["raw"]["version"] == 1


def test_api_presentation_name_roundtrips_and_rejects_a_different_slug(
        clientroom):
    app = srv.app.test_client()
    profile = dict(GOOD_PROFILE, client_name="mark43", display_name="Mark43")

    saved = app.post("/api/onboarding", json={
        "client_name": "mark43", "profile": profile})
    assert saved.status_code == 200
    assert saved.get_json()["files"]["profile"]["raw"]["display_name"] == "Mark43"

    view = app.get("/api/onboarding/mark43").get_json()
    assert view["client_name"] == "mark43"
    assert view["files"]["profile"]["raw"]["display_name"] == "Mark43"
    from tools.capability import client_display_name
    assert client_display_name("mark43") == "Mark43"

    rejected = app.post("/api/onboarding", json={
        "client_name": "mark43",
        "profile": dict(profile, display_name="Mark 43"),
        "expected_shas": {
            "profile": view["files"]["profile"]["sha256"],
        },
    })
    assert rejected.status_code == 400
    assert "preserve the client_name slug" in rejected.get_json()["errors"]["profile"]
    unchanged = app.get("/api/onboarding/mark43").get_json()
    assert unchanged["files"]["profile"]["raw"]["display_name"] == "Mark43"


def test_api_validation_errors_return_400_with_per_file_text(clientroom):
    app = srv.app.test_client()
    r = app.post("/api/onboarding", json={
        "client_name": "Testco",
        "taxonomy": dict(GOOD_TAXONOMY, version=0)})
    assert r.status_code == 400
    errors = r.get_json()["errors"]
    assert "taxonomy" in errors
    assert "version" in errors["taxonomy"]


def test_api_conflict_returns_409(clientroom):
    app = srv.app.test_client()
    assert app.post("/api/onboarding", json={
        "client_name": "Testco", "profile": GOOD_PROFILE}).status_code == 200
    r = app.post("/api/onboarding", json={
        "client_name": "Testco", "profile": GOOD_PROFILE,
        "expected_shas": {"profile": "stale"}})
    assert r.status_code == 409
    assert "reload before saving" in r.get_json()["error"]


def test_unpopulated_client_view_reports_the_gate_text(clientroom):
    app = srv.app.test_client()
    view = app.get("/api/onboarding/brand_new_co").get_json()
    assert view["sweep_ready"] is False
    assert "No profile, no sweep" in view["sweep_gate_error"]


def test_control_room_serves_the_onboarding_door(clientroom):
    """C4 amendment (2026-07-18): one client-creation entry point. The
    served page carries + New Client as the only door; the onboarding
    dialog MACHINERY (function, validated-save wiring) stays intact for
    V4.4 while the separate hub button is gone."""
    app = srv.app.test_client()
    html = app.get("/").data.decode()
    assert "openOnboardingDialog" in html          # machinery intact
    assert "+ New Client" in html                  # the one door
    assert "+ Add client" not in html              # redundant entry removed
    assert "/api/onboarding" in html
    assert "the messages above are the validators" in html


def test_profile_missing_links_to_the_flow_not_a_scaffold_command():
    from tools.capability import ProfileMissing, require_profile
    with pytest.raises(ProfileMissing) as e:
        require_profile("no_such_client_zzz")
    text = str(e.value)
    assert "No profile, no sweep" in text          # doctrine phrase, pinned
    assert "+ New Client" in text                  # C4: the one entry point
    assert "scaffold" not in text.lower()


def test_draft_assist_is_prefill_only_and_validator_checked(clientroom,
                                                            monkeypatch):
    from agents.decisions import maxplan_cli
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    monkeypatch.setattr(maxplan_cli, "run_claude", lambda prompt, **kw: (
        '{"profile": {"client_name": "Testco", "capability_terms": '
        '{"core": ["video wall"], "adjacent": [], "excluded": []}, '
        '"naics_boundary": ["334310"]}, '
        '"taxonomy": {"client_name": "Testco", "version": 0, '
        '"updated": "2026-07-17", "core": []}}'))
    app = srv.app.test_client()
    r = app.post("/api/onboarding/draft", json={
        "client_name": "Testco", "materials": "https://testco.example"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["suggestion"]["profile"]["client_name"] == "Testco"
    # the bad taxonomy (version 0) is caught by the SAME runner validator
    assert "taxonomy" in body["validation_errors"]
    assert "nothing was written" in body["note"]
    assert not os.path.exists(ob.file_path("Testco", "profile"))


def test_draft_assist_absent_cli_degrades_cleanly(clientroom, monkeypatch):
    from agents.decisions import maxplan_cli
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: False)
    app = srv.app.test_client()
    r = app.post("/api/onboarding/draft", json={
        "client_name": "Testco", "materials": "x"})
    assert r.status_code == 503
    assert "deterministic form flow is unaffected" in r.get_json()["error"]
