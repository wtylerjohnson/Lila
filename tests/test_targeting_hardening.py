"""Hardening steps 1-3: isolation, R2 recovery, mandatory screening.

Each block corresponds to a numbered step of the operator's build sequence
and proves the specific guarantee that step exists to create.

  1. Apollo is disarmed and isolated for every test by default, and arming
     it is visible in a test's signature.
  2. Both R2 schemas produce equivalent targeting specs, and the 30 legacy
     alerts on disk recover.
  3. Screening is a PUBLICATION requirement: an unscreened person cannot
     render or export, the screen and its cautions survive storage, and an
     unknown title is never silently promoted to a persona tier.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import person_screen as ps  # noqa: E402
from agents.golden_press import targets_store  # noqa: E402
from agents.golden_press.decision_rules import build_decisions  # noqa: E402
from agents.golden_press.press import _press_stamp  # noqa: E402
from agents.golden_press.records import EvidencePack  # noqa: E402
from agents.golden_press.targeting_rules import (  # noqa: E402
    build_targeting, normalize_r2_alerts, tier_for_title)
from tools import apollo_targets  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PACKS = REPO / "data" / "state" / "candidate_review_v1"


# ===== STEP 1 · isolation ================================================== #
def test_apollo_is_disarmed_for_every_test_by_default():
    """The autouse fixture removes the key and forces both switches off, so
    a developer with a real key cannot have the suite spend their balance."""
    assert os.environ.get("APOLLO_API_KEY") is None
    assert apollo_targets.apollo_enabled() is False
    assert apollo_targets.phone_reveal_allowed() is False


def test_the_targets_store_is_redirected_away_from_production():
    path = targets_store.store_dir()
    assert "candidate_review_v1" not in str(path)
    assert str(path) != str(REPO / "data" / "state" / "targets")


def test_arming_apollo_is_visible_in_a_test_signature(apollo_enabled_env):
    """The opt-in fixture is the ONLY way a test gets an armed Apollo, so
    `grep apollo_enabled_env tests/` enumerates everything that could spend.
    It arms the switch and a throwaway key and still leaves the phone reveal
    off, because that path costs eight times as much."""
    assert apollo_targets.apollo_enabled() is True
    assert apollo_targets.api_key() == apollo_enabled_env["key"]
    assert apollo_targets.phone_reveal_allowed() is False


def test_the_apollo_network_seam_is_watched_not_merely_the_shared_client():
    """tools/apollo_targets builds its own urllib request and never touches
    tools.api._http, so the repo's live-call observer was blind to it. The
    conftest wraps this seam by name."""
    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "apollo_targets" in conftest
    assert "_http_post" in conftest
    assert "APOLLO_API_KEY" in conftest


# ===== STEP 2 · R2 recovery ================================================ #
_LEGACY = {
    "kind": "pack_award", "record_id": "NOTICE1",
    "title": "RECEIVER-TRANSMITTER", "url": "https://sam.gov/opp/NOTICE1/view",
    "is_award": True, "agency": "DEFENSE LOGISTICS AGENCY",
    "competitor": "ROCKWELL COLLINS, INC.",
    "matched_sentence": "Qualified Source(s): ROCKWELL COLLINS, INC.",
    "sole_source_phrase": None,
    "client_history_record_ids": ["AWARD1"],
}


def _pack_with(record_id="AWARD1"):
    from agents.golden_press.records import GoldenRecord
    return EvidencePack(
        client_name="Acme", generated_at="2026-07-27T12:00:00Z",
        records=[GoldenRecord(
            record_id=record_id, lane="L2_entity_award", title="support",
            agency="DEFENSE LOGISTICS AGENCY", sub_agency="DLA Aviation",
            recipient="Acme", obligated_dollars=1000.0, period_end="2026-12-31",
            url="https://www.usaspending.gov/award/CONT_AWD_" + record_id)],
        research={"entities": {"competitor": ["ROCKWELL COLLINS, INC."]}})


def test_both_r2_schemas_normalise_to_the_same_joined_evidence():
    """The legacy key carries ids, the current key carries rows. They must
    reduce to the same thing or half the book silently loses its specs."""
    pack = _pack_with()
    receipt: dict = {}
    out = normalize_r2_alerts(pack, [_LEGACY], receipt)
    assert receipt["r2_legacy_migrated"] == 1
    assert receipt["r2_ids_unresolvable"] == []
    rows = out[0]["client_history_records"]
    assert [r["record_id"] for r in rows] == ["AWARD1"]
    assert rows[0]["url"].endswith("CONT_AWD_AWARD1")
    # the flattened legacy fields are mapped, not read in two dialects
    assert out[0]["source"]["kind"] == "pack_award"
    assert out[0]["context"] == "award"
    assert out[0]["sentence"].startswith("Qualified Source(s)")


def test_a_current_shape_alert_passes_through_untouched():
    """NEGATIVE: the normaliser must not rewrite an alert that is already
    current, or a replay would stop being byte-stable."""
    current = {"competitor": "X", "agency": "Y", "context": "award",
               "client_history_records": [{"record_id": "AWARD1"}]}
    receipt: dict = {}
    out = normalize_r2_alerts(_pack_with(), [current], receipt)
    assert out[0] is current
    assert receipt["r2_legacy_migrated"] == 0


def test_an_unresolvable_legacy_id_is_receipted_never_invented():
    """A record the pack does not carry cannot be joined. Fabricating one
    would defeat the join law the whole band rests on."""
    receipt: dict = {}
    alert = dict(_LEGACY, client_history_record_ids=["GHOST"])
    out = normalize_r2_alerts(_pack_with(), [alert], receipt)
    assert out[0]["client_history_records"] == []
    assert receipt["r2_ids_unresolvable"] == ["GHOST"]


@pytest.mark.parametrize("slug", ["riverbed", "thinklogical", "varonis"])
def test_the_thirty_legacy_alerts_on_disk_recover(slug):
    """The measured defect (2026-08-05): 30 alerts across three clients,
    every one under the legacy key, every one producing no spec.

    THE MECHANISM IS THE INVARIANT, NEVER A COUNT. Alert counts move every
    time a legitimate press rebuilds a pack (the Gate E re-presses moved
    riverbed 7 -> 16 mid-suite and broke a pinned count twice in one day).
    What must hold: every stored alert migrates, none resolves to a ghost,
    and displacement specs come out the other side.
    """
    path = PACKS / slug / f"{slug}.golden_report.evidence_pack.json"
    if not path.exists():
        pytest.skip(f"{slug} pack not on this machine")
    pack = EvidencePack.model_validate(
        json.loads(path.read_text(encoding="utf-8")))
    stored = (pack.decisions or {}).get("r2") or {}
    alerts = stored.get("alerts") or []
    if not alerts:
        pytest.skip(f"{slug}: the current press stores no R2 alerts; the "
                    "migration mechanism is covered by the synthetic-legacy "
                    "tests above, and this disk check re-arms whenever a "
                    "press stores alerts again")
    # A pack stops being legacy the moment a modern press rewrites it, and
    # Gate E re-presses did exactly that mid-day. Whichever schema each
    # alert carries, it must go through whole: legacy rows migrate, current
    # rows pass, nothing resolves to a ghost.
    legacy = sum(1 for a in alerts if "client_history_record_ids" in a)
    current = sum(1 for a in alerts if "client_history_records" in a
                  and "client_history_record_ids" not in a)
    assert legacy + current == len(alerts), (
        f"{slug}: alert rows carrying neither history key")

    stamp = _press_stamp(pack)
    fresh = build_decisions(
        pack, today=date.fromisoformat(stamp) if stamp else None)
    fresh["r2"] = stored
    pack.decisions = fresh
    out = build_targeting(pack)
    receipt = out["receipt"]
    assert receipt["r2_legacy_migrated"] == legacy
    assert receipt["r2_ids_unresolvable"] == []
    displacement = [s for s in out["specs"] if s["row_class"] == "displacement"]
    assert displacement, f"{slug} recovered no displacement spec"


# ===== STEP 3 · screening is a publication requirement ===================== #
_ACCEPTED = {"state": ps.ACCEPTED, "verdict": "pass", "findings": [],
             "cautions": [], "source_class": ps.FEDERAL_COMPONENT}


def _contact(**kw):
    base = {"name": "Dana Reyes", "spec_id": "s1", "title": "Contracting Officer",
            "provenance": {"class": "apollo", "retrieved_at": "2026-08-06"},
            "screen": dict(_ACCEPTED)}
    base.update(kw)
    return base


def test_an_unscreened_person_cannot_render_or_export():
    """THE P0. A row with no screen block is pending_screen and is withheld
    from both the artifact and the export."""
    payload = {"contacts": [_contact(screen=None)]}
    rows, dropped = targets_store.admissible(payload, spec_ids=["s1"])
    assert rows == []
    assert dropped["not_screened"] == 1
    assert dropped["withheld_states"][ps.PENDING_SCREEN] == 1


@pytest.mark.parametrize("state,renders", [
    (ps.ACCEPTED, True), (ps.ACCEPTED_WITH_FLAGS, True),
    (ps.REJECTED, False), (ps.PENDING_SCREEN, False), ("invented", False)])
def test_only_accepted_states_reach_a_client(state, renders):
    payload = {"contacts": [_contact(screen=dict(_ACCEPTED, state=state))]}
    rows, _ = targets_store.admissible(payload, spec_ids=["s1"])
    assert bool(rows) is renders


def test_screening_flags_survive_storage_and_export():
    """Seven of the eight rules can only FLAG. Previously admissible()
    rebuilt rows from a fixed key list and dropped the screen entirely, so
    every caution was computed, stored, and discarded before the page."""
    screen = dict(_ACCEPTED, state=ps.ACCEPTED_WITH_FLAGS,
                  cautions=["the work address carries status 'extrapolated'"])
    rows, _ = targets_store.admissible(
        {"contacts": [_contact(screen=screen)]}, spec_ids=["s1"])
    assert rows[0]["screen"]["state"] == ps.ACCEPTED_WITH_FLAGS
    assert rows[0]["screen"]["cautions"]

    targeting = {"specs": [{"spec_id": "s1", "row_class": "renewal",
                            "buying_component": "IRS", "rationale": "why",
                            "join_records": []}],
                 "contacts": [_contact(screen=screen, pass_label="Pass 1")]}
    export = targets_store.sequence_export_rows(targeting)
    assert export[0]["Screening"] == ps.ACCEPTED_WITH_FLAGS
    assert "extrapolated" in export[0]["Cautions"]


def test_pass_label_survives_into_the_export():
    """It was read by the exporter and never copied by admissible(), so the
    Pass column was permanently blank on live data."""
    targeting = {"specs": [{"spec_id": "s1", "row_class": "renewal",
                            "buying_component": "IRS", "rationale": "why",
                            "join_records": []}],
                 "contacts": [_contact(pass_label="Pass 2")]}
    assert targets_store.sequence_export_rows(targeting)[0]["Pass"] == "Pass 2"


def test_source_refresh_and_retrieval_dates_stay_distinct():
    """Apollo states its own per-record freshness; we state when we fetched.
    Collapsing them makes a year-old record look fetched today."""
    rows, _ = targets_store.admissible(
        {"contacts": [_contact(apollo_refreshed_at="2025-08-21")]},
        spec_ids=["s1"])
    assert rows[0]["source_refreshed_at"] == "2025-08-21"
    assert rows[0]["provenance"]["retrieved_at"] == "2026-08-06"


# ---- source-appropriate policies ------------------------------------------ #
def test_federal_geography_rules_never_run_on_a_reseller():
    """A channel manager abroad working for a US reseller is a normal
    partner contact. Applying federal-service geography logic rejects them
    for living where their employer operates."""
    person = {"name": "Priya S", "country": "India",
              "time_zone": "Asia/Kolkata", "email": "priya@techanax.com",
              "organization": {"name": "TECHANAX LLC",
                               "primary_domain": "techanax.com"},
              "employment_history": [
                  {"current": True, "organization_name": "TECHANAX LLC"},
                  {"current": False, "organization_name": "Infosys"},
                  {"current": False, "organization_name": "Wipro"}]}
    spec = {"target_org": {"kind": "paper_holder", "name": "TECHANAX LLC"}}
    out = ps.screen_person(person, spec)
    assert out["source_class"] == ps.RESELLER
    assert out["state"] != ps.REJECTED
    assert "S1" not in out["rules_in_force"]
    assert "S2" not in out["rules_in_force"]


def test_the_federal_screen_still_rejects_the_known_false_positive():
    """The control: the same rules that must spare a reseller must still
    remove a person who is not at the federal component they claim."""
    from tests.test_person_screen import LYOVIN, COMPONENT
    out = ps.screen_person(LYOVIN, COMPONENT, today=date(2026, 8, 6))
    assert out["state"] == ps.REJECTED
    assert out["source_class"] == ps.FEDERAL_COMPONENT


def test_a_notice_poc_is_screened_but_not_interrogated_for_identity():
    """The government published this person on a notice, so identity is not
    in question; only deliverability and freshness are tested."""
    person = {"name": "K Officer", "email": "k.officer@dla.mil",
              "email_status": "verified"}
    spec = {"target_org": {"kind": "buying_component", "name": "DLA"}}
    out = ps.screen_person(person, spec, source_class=ps.NOTICE_POC)
    assert out["state"] == ps.ACCEPTED
    assert set(out["rules_in_force"]) == {"S6", "S8"}


def test_an_unknown_source_class_raises_rather_than_screening_loosely():
    with pytest.raises(ValueError, match="unknown screening source class"):
        ps.screen_person({}, {}, source_class="vibes")


# ---- no silent tier default ----------------------------------------------- #
def test_an_unknown_title_is_unattributed_never_the_first_rung():
    """The old behaviour returned sought[0], so 'Supervisory IT Specialist'
    was presented as a confident tier 1 when nothing had matched."""
    spec = {"seeks_tiers": [1, 2],
            "persona_titles": {"1": ["Program Manager"],
                               "2": ["Contracting Officer"]}}
    assert tier_for_title(spec, "Supervisory IT Specialist") is None
    assert tier_for_title(spec, "") is None
    assert tier_for_title(spec, "Program Manager") == 1
    assert tier_for_title(spec, "Supervisory Contracting Officer") == 2


def test_an_unattributed_tier_exports_as_unattributed_not_as_a_number():
    targeting = {"specs": [{"spec_id": "s1", "row_class": "renewal",
                            "buying_component": "IRS", "rationale": "why",
                            "join_records": []}],
                 "contacts": [_contact(tier=None)]}
    assert targets_store.sequence_export_rows(
        targeting)[0]["Persona Tier"] == "unattributed"
