"""Band 09, the targeting action layer (REPORT_CONTRACT.md amendment v1.4,
2026-08-05).

Every band above 09 establishes what is true; this one states who to call
about it. The laws it has to keep, and what each test here proves:

  1. the band exists because the CONTRACT says so: §1 position 09, after 08
     method and before the footer, numbered from its render position
  2. JOIN LAW, and it is literal. A call hangs only on a record id the bands
     above ACTUALLY printed, so a spec whose evidence was capped out of
     bands 01 to 07 is withheld and the count is stated
  3. NO PROVENANCE, NO RENDER. An identity with no readable class and date
     is dropped by the store's own admission law and never reaches the page
  4. THE RECEIPT DECIDES THE ZERO. Attempted with zero results is a
     certified zero; no receipt at all is not a zero, and the validator
     refuses a tested-zero sentence over an unattempted lane
  5. the checks are driven from the contract's own text, so a renamed
     posture is a contract edit rather than a silent drift
  6. contacts are ENRICHMENT: they are never asserted as federal records
     and never enter the primary link tally

The two press proofs at the bottom are the deliverable ones: a real press
off a pack with stored contacts and a real press off an empty store, both
certified, both making zero live calls.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import contract as contract_mod  # noqa: E402
from agents.golden_press import render as render_mod  # noqa: E402
from agents.golden_press import targets_store  # noqa: E402
from agents.golden_press import validate as validate_mod  # noqa: E402
from agents.golden_press.decision_rules import build_decisions  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402
from agents.golden_press.targeting_rules import build_targeting  # noqa: E402
from agents.golden_press.validate import validate_press  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _award(**kw):
    base = dict(record_id="GS35F001", lane="L2_entity_award",
                title="Network performance monitoring support",
                description="Network performance monitoring support",
                agency="Department of the Treasury",
                sub_agency="Internal Revenue Service",
                recipient="Carahsoft Technology",
                obligated_dollars=1_920_000.0,
                url="https://www.usaspending.gov/award/CONT_AWD_GS35F001",
                period_end="2026-08-31", entity_hits=["AcmeFlow"])
    base.update(kw)
    return GoldenRecord(**base)


def _rival(**kw):
    base = dict(record_id="RIV1", recipient="Rival Prime Inc",
                entity_hits=["OtherMonitor"], obligated_dollars=900_000.0,
                period_end="2026-11-30",
                url="https://www.usaspending.gov/award/CONT_AWD_RIV1")
    base.update(kw)
    return _award(**base)


def _pack(records=None, **kw):
    kw.setdefault("research", {"entities": {"competitor": ["OtherMonitor"],
                                            "product": ["AcmeFlow"]}})
    pack = EvidencePack(client_name="Acme Networks",
                        generated_at="2026-07-27T12:00:00Z",
                        records=records or [_award(), _rival()], **kw)
    pack.decisions = build_decisions(pack)
    return pack


def _contact(spec_id, **kw):
    base = {
        "contact_id": "a1", "spec_id": spec_id, "tier": 1,
        "name": "Dana Reyes", "title": "Contracting Officer",
        "organization": "Internal Revenue Service",
        "email": "dana.reyes@irs.gov",
        "provenance": {"class": "apollo", "retrieved_at": "2026-07-27",
                       "source": "apollo.mixed_people_search"},
        "screen": {"state": "accepted", "verdict": "pass", "source_class": "federal_component", "findings": [], "cautions": []},
    }
    base.update(kw)
    return base


def _stored(pack, contacts, *, attempted=True, result_count=None):
    receipt = {"attempted": attempted,
               "source": "apollo.mixed_people_search",
               "retrieved_at": "2026-07-27",
               "result_count": (len(contacts) if result_count is None
                                else result_count),
               "calls_made": 1, "call_cap": 25}
    return {"receipt": receipt, "contacts": contacts}


def _with_contacts(pack, make_contacts):
    """Attach targeting with contacts bound to the pack's own spec ids."""
    base = build_targeting(pack)
    contacts = make_contacts([s["spec_id"] for s in base["specs"]])
    pack.targeting = build_targeting(pack, contacts=_stored(pack, contacts))
    return pack


def _band(content, band_id="targeting"):
    m = re.search(rf'<section class="band" id="{band_id}".*?</section>',
                  content, re.S)
    assert m, f"band {band_id!r} did not render"
    return m.group(0)


# ---- the contract puts the band here --------------------------------------- #
def test_the_contract_carries_band_09_after_method_and_before_the_footer():
    bands = {row.band_id: row for row in contract_mod.load_contract_bands()}
    assert bands["targeting"].number == 9
    assert bands["method"].number == 8
    assert bands["footer"].number is None
    assert contract_mod.contract_section_ids()[-1] == "targeting"


def test_the_band_renders_last_and_numbers_itself_from_its_position():
    content = render_mod.render_content_region(_pack(), prose={})
    assert content.index('id="method"') < content.index('id="targeting"')
    assert content.index('id="targeting"') < content.index('class="foot"')
    assert '<div class="b-no">09</div>' in _band(content)


def test_the_validator_reads_band_09s_laws_from_the_contract_text():
    """Contract-driven, not restated: rename a posture in the contract and
    the checker follows it."""
    assert contract_mod.band_postures("targeting") == (
        "ENRICHMENT RECEIPT", "NO CONTACTS RESOLVED",
        "TARGETING SUPPLY NOT RUN", "TARGETING · NONE",
        "TARGETING NOT COMPUTED")
    assert contract_mod.band_provenance_classes("targeting") == (
        "apollo", "operator")
    assert "already rendered" in contract_mod.band_law("targeting", "JOIN LAW")


# ---- specs render, joined and cited ---------------------------------------- #
def test_specs_render_with_their_persona_tiers_and_anchored_join():
    pack = _pack()
    pack.targeting = build_targeting(pack)
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert 'data-targeting-table="1"' in band
    assert "Internal Revenue Service" in band
    assert "TIER 1" in band and "TIER 2" in band
    # LINKAGE LAW inside the band: an id renders only in its own anchor
    assert 'href="https://www.usaspending.gov/award/CONT_AWD_GS35F001"' in band
    assert validate_press(content, content, pack)["ok"]


def test_the_persona_ladder_prints_once_and_only_the_rungs_sought():
    """Tier 3 is defined by the ladder and sought by no v1 rule, so it does
    not render as though someone were being looked for."""
    pack = _pack()
    pack.targeting = build_targeting(pack)
    band = _band(render_mod.render_content_region(pack, prose={}))
    assert band.count("TIER 1 ·") >= 1
    assert "TECHNICAL EVALUATOR" not in band
    assert "Enterprise Architect" not in band


# ---- the join law is literal ----------------------------------------------- #
def test_a_spec_whose_evidence_the_bands_never_printed_is_withheld():
    """Band 05 caps its buyer list, so a joined record can be real and still
    unprinted. The contract says a row that joins no RENDERED record does
    not render; the count is stated, never silent."""
    pack = _pack()
    pack.targeting = build_targeting(pack)
    # force the join onto an id nothing above band 09 renders
    for spec in pack.targeting["specs"]:
        for row in spec["join_records"]:
            row["record_id"] = "UNRENDERED9"
    band = _band(render_mod.render_content_region(pack, prose={}))
    assert 'data-targeting-table="1"' not in band
    assert "TARGETING · NONE" in band
    assert "the bands above did not print" in band


def test_the_validator_catches_a_call_hung_on_an_unrendered_record():
    """NEGATIVE for the join law at the validator: a hand-built artifact
    that prints an id in band 09 which no evidence band carries."""
    pack = _pack()
    pack.targeting = build_targeting(pack)
    content = render_mod.render_content_region(pack, prose={})
    # move an evidence band's only citation of RIV1 out of the artifact by
    # renaming the band, then keep the id in band 09
    broken = content.replace('<section class="band" id="agencies"',
                             '<section class="band" id="agenciesX"', 1)
    broken = broken.replace('id="clocks"', 'id="clocksX"', 1)
    broken = broken.replace('id="competitive"', 'id="competitiveX"', 1)
    broken = broken.replace('id="paper"', 'id="paperX"', 1)
    broken = broken.replace('id="decisions"', 'id="decisionsX"', 1)
    broken = broken.replace('id="forward"', 'id="forwardX"', 1)
    broken = broken.replace('id="events"', 'id="eventsX"', 1)
    rules = {v["rule"] for v in
             validate_mod.check_targeting_band(broken, pack)}
    assert "targeting_join_unrendered" in rules


# ---- no provenance, no render ---------------------------------------------- #
def test_an_identity_with_no_provenance_never_reaches_the_page():
    pack = _with_contacts(_pack(), lambda ids: [
        _contact(ids[0]),
        {"contact_id": "a2", "spec_id": ids[0], "tier": 1,
         "name": "Unlabelled Person", "title": "Ghost",
         "organization": "Nowhere"},
    ])
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "Dana Reyes" in band
    assert "Unlabelled Person" not in band
    assert "withheld for missing provenance" in band
    assert validate_press(content, content, pack)["ok"]


@pytest.mark.parametrize("bad", [
    {"class": "guess", "retrieved_at": "2026-07-27"},
    {"class": "apollo", "retrieved_at": ""},
    {"class": "apollo"},
    "apollo",
])
def test_the_store_refuses_every_unreadable_provenance_shape(bad):
    rows, dropped = targets_store.admissible(
        {"contacts": [_contact("s1", provenance=bad)]})
    assert rows == []
    assert dropped["no_provenance"] == 1


def test_a_rendered_contact_states_its_class_and_its_retrieved_date():
    pack = _with_contacts(_pack(), lambda ids: [_contact(ids[0])])
    band = _band(render_mod.render_content_region(pack, prose={}))
    assert "APOLLO" in band and "27 JUL 2026" in band
    assert 'data-targeting-contacts="1"' in band
    # the identity is labeled enrichment, never asserted as a record
    assert "never a federal record itself" in band


def test_the_validator_catches_a_contact_row_with_no_provenance_cell():
    pack = _with_contacts(_pack(), lambda ids: [_contact(ids[0])])
    content = render_mod.render_content_region(pack, prose={})
    broken = content.replace('APOLLO · ', '')
    rules = {v["rule"] for v in
             validate_mod.check_targeting_band(broken, pack)}
    assert "targeting_identity_without_provenance" in rules


def test_the_validator_catches_a_provenance_class_with_no_date():
    pack = _with_contacts(_pack(), lambda ids: [_contact(ids[0])])
    content = render_mod.render_content_region(pack, prose={})
    broken = re.sub(r"APOLLO · 27 JUL 2026", "APOLLO", content)
    rules = {v["rule"] for v in
             validate_mod.check_targeting_band(broken, pack)}
    assert "targeting_identity_without_retrieved_date" in rules


# ---- the receipt decides the zero ------------------------------------------ #
def test_attempted_with_zero_results_is_a_certified_zero():
    pack = _pack()
    pack.targeting = build_targeting(
        pack, contacts=_stored(pack, [], result_count=0))
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "NO CONTACTS RESOLVED" in band
    assert "0 identity result(s)" in band
    assert "not an absence of work" in band
    assert validate_press(content, content, pack)["ok"]


def test_no_receipt_states_the_posture_and_never_the_tested_zero():
    pack = _pack()
    pack.targeting = build_targeting(pack)
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "TARGETING SUPPLY NOT RUN" in band
    assert "NO CONTACTS RESOLVED" not in band
    assert "it is not a zero" in band
    assert validate_press(content, content, pack)["ok"]


def test_the_validator_refuses_a_tested_zero_over_an_unattempted_lane():
    """NEGATIVE for the zero-state law, the competitor_zero_untested shape
    applied to people: printing the certified sentence without a receipt
    that says an attempt happened is a violation."""
    pack = _pack()
    pack.targeting = build_targeting(pack)
    content = render_mod.render_content_region(pack, prose={})
    faked = content.replace("TARGETING SUPPLY NOT RUN",
                            "NO CONTACTS RESOLVED")
    rules = {v["rule"] for v in
             validate_mod.check_targeting_band(faked, pack)}
    assert "targeting_zero_untested" in rules


def test_a_pack_with_no_decision_row_states_targeting_none():
    pack = EvidencePack(client_name="Acme Networks",
                        generated_at="2026-07-27T12:00:00Z",
                        records=[_award()],
                        research={"entities": {"product": ["AcmeFlow"]}})
    pack.decisions = build_decisions(pack)
    pack.targeting = build_targeting(pack)
    content = render_mod.render_content_region(pack, prose={})
    assert "TARGETING · NONE" in _band(content)
    assert validate_press(content, content, pack)["ok"]


def test_a_pack_pressed_before_the_rules_says_so_which_is_not_a_zero():
    pack = _pack()
    pack.targeting = None
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert "TARGETING NOT COMPUTED" in band
    assert "were not computed" in band
    assert "TARGETING · NONE" not in band
    assert "NO CONTACTS RESOLVED" not in band
    assert validate_press(content, content, pack)["ok"]


def test_the_validator_catches_more_contact_rows_than_the_pack_admits():
    """NEGATIVE for the renderer itself: if it ever stopped filtering, the
    extra rows are caught against the store's own admission law."""
    pack = _with_contacts(_pack(), lambda ids: [_contact(ids[0])])
    content = render_mod.render_content_region(pack, prose={})
    row = re.search(r'data-targeting-contacts="1".*?<tbody>(<tr>.*?</tr>)',
                    content, re.S).group(1)
    smuggled = content.replace(row, row + row.replace("Dana Reyes",
                                                      "Smuggled Person"), 1)
    rules = {v["rule"] for v in
             validate_mod.check_targeting_band(smuggled, pack)}
    assert "targeting_contact_rows_exceed_admissible" in rules


def test_the_spec_table_caps_its_rows_and_states_the_cap():
    """Measured on the live packs: adjacency produces one row per forecast
    at a single reseller. The page needs a bound and the bound is said out
    loud, in the one dialect every capped lane here speaks."""
    pack = _pack()
    pack.targeting = build_targeting(pack)
    template = pack.targeting["specs"][0]
    pack.targeting["specs"] = [
        dict(template, spec_id=f"{template['spec_id']}-{i}",
             target_org={"kind": "buying_component", "name": f"Bureau {i}"},
             rationale=f"Bureau {i} holds current client paper closing "
                       f"31 AUG 2026, so the renewal is decided here.")
        for i in range(20)]
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert band.count("<tr>") == render_mod._TARGETING_SPEC_CAP + 1  # + header
    assert "8 further targeting spec(s)" in band
    assert "never silent" in band
    assert validate_press(content, content, pack)["ok"]


def test_two_specs_reaching_the_same_sentence_print_it_once():
    """The stated-once precedent from the competitive band's motion
    rationale: identical claim copy on two rows would read as repeated
    prose and fail the artifact. Both rows still stand."""
    pack = _pack()
    pack.targeting = build_targeting(pack)
    template = pack.targeting["specs"][0]
    pack.targeting["specs"] = [
        dict(template, spec_id=f"{template['spec_id']}-{i}",
             target_org={"kind": "buying_component", "name": f"Bureau {i}"})
        for i in range(2)]
    content = render_mod.render_content_region(pack, prose={})
    band = _band(content)
    assert band.count("so the renewal is decided here") == 1
    assert "Bureau 0" in band and "Bureau 1" in band
    assert validate_press(content, content, pack)["ok"]


def test_a_route_serving_several_specs_renders_once_with_the_count():
    pack = _with_contacts(_pack(), lambda ids: [
        _contact(ids[0], spec_ids=[ids[0], "t3-01-other"])])
    band = _band(render_mod.render_content_region(pack, prose={}))
    assert band.count("Dana Reyes") == 1
    assert "serves 2 specs" in band


# ---- house style holds in the new band ------------------------------------- #
def test_the_band_carries_no_em_dash_and_no_banned_vocabulary():
    pack = _with_contacts(_pack(), lambda ids: [_contact(ids[0])])
    content = render_mod.render_content_region(pack, prose={})
    assert validate_mod.check_emdash(content) == []
    assert validate_mod.check_banned_vocabulary(content) == []


def test_contacts_never_enter_the_primary_link_tally():
    """A contact is a route, not evidence. No mailto anchor, and the link
    legend's published-contacts count stays at zero."""
    pack = _with_contacts(_pack(), lambda ids: [_contact(ids[0])])
    content = render_mod.render_content_region(pack, prose={})
    assert "mailto:" not in _band(content)
    assert "published contacts 0" in content


# ---- the Apollo-ready export is an operator tool, not an artifact ---------- #
def test_the_sequence_export_carries_the_join_and_only_admissible_rows():
    pack = _with_contacts(_pack(), lambda ids: [
        _contact(ids[0]),
        {"contact_id": "a2", "spec_id": ids[0], "name": "Unlabelled Person"},
    ])
    rows = targets_store.sequence_export_rows(pack.targeting)
    assert len(rows) == 1
    row = rows[0]
    assert row["First Name"] == "Dana" and row["Last Name"] == "Reyes"
    assert row["Provenance Class"] == "apollo"
    assert row["Retrieved At"] == "2026-07-27"
    assert "GS35F001" in row["Joined Record IDs"]
    assert "usaspending.gov" in row["Joined Record URLs"]
    assert row["Why This Call"]


def test_the_sequence_export_writes_a_header_only_file_at_zero_contacts(
        tmp_path):
    pack = _pack()
    pack.targeting = build_targeting(pack)
    path = tmp_path / "x.apollo_targets.csv"
    assert targets_store.write_sequence_csv(path, pack.targeting) == 0
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("First Name,Last Name,Email")


# ---- THE TWO PRESS PROOFS -------------------------------------------------- #
def _press_fixture(monkeypatch, pack, store_dir):
    import agents.golden_press.press as press_mod
    import agents.review as review_mod

    class _Strategy:
        """Enough of the strategy surface for the competitive build, which
        grows a roster with model_copy when discovery confirms a rival."""

        def __init__(self, research_entities=None):
            self.research_entities = list(research_entities or [])
            self.client_name = "Acme Networks"

        def model_copy(self, *, update=None):
            return _Strategy((update or {}).get(
                "research_entities", self.research_entities))

    class _Packet:
        strategy = _Strategy()
        status = type("St", (), {"value": "approved"})()

    monkeypatch.setattr(review_mod, "load_packet", lambda c: _Packet())
    monkeypatch.setattr(press_mod, "build_evidence_pack",
                        lambda *a, **k: pack)
    import tools.relevance.scope as scope_mod
    monkeypatch.setattr(scope_mod, "load_engagement_scope", lambda c: None)
    monkeypatch.setenv(targets_store.STORE_DIR_ENV, str(store_dir))
    # keep the proof off the operator's real Desktop
    monkeypatch.setenv("LILA_DESKTOP_ROOT", str(store_dir.parent))
    # ZERO LIVE CALLS AT PRESS TIME, proved by poisoning the only seam that
    # could make one. A press that touches Apollo now dies loudly instead of
    # quietly costing credits.
    import tools.apollo_targets as apollo_mod

    def _forbidden(*a, **k):
        raise AssertionError("the press made a live Apollo call")

    monkeypatch.setattr(apollo_mod, "_http_post", _forbidden)
    return press_mod


def _fake_run(prompt, *, system=None, model=None, timeout_s=None, **kw):
    return json.dumps({"hero_context": "This account sits in one bureau "
                                       "with a live clock and a named rival "
                                       "already inside the estate."})


def test_press_proof_one_a_pack_with_stored_contacts_certifies(
        tmp_path, monkeypatch):
    """PRESS PROOF 1: the supply step has run, the store holds a labeled
    identity, and the press renders it in Band 09 without calling anyone."""
    pack = _pack()
    store_dir = tmp_path / "targets"
    store_dir.mkdir()
    spec_ids = [s["spec_id"] for s in build_targeting(pack)["specs"]]
    assert spec_ids, "the proof needs at least one spec to hang a contact on"
    (store_dir / "acme_networks.contacts.json").write_text(
        json.dumps(_stored(pack, [_contact(spec_ids[0])])), encoding="utf-8")

    press_mod = _press_fixture(monkeypatch, pack, store_dir)
    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=_fake_run, log=lambda m: None)

    assert result["certified"] is True, result["validation"]["violations"]
    html = Path(result["client_path"]).read_text(encoding="utf-8")
    band = _band(html)
    assert "Dana Reyes" in band
    assert "APOLLO · 27 JUL 2026" in band
    assert "ENRICHMENT RECEIPT" in band
    # the Apollo-ready export lands beside the client artifact and is not
    # the certified artifact
    csv_path = tmp_path / "acme_networks.golden_report.apollo_targets.csv"
    assert csv_path.exists()
    assert "Dana,Reyes" in csv_path.read_text(encoding="utf-8")
    assert not str(csv_path).endswith(".html")


def test_press_proof_two_an_empty_store_certifies_on_the_stated_posture(
        tmp_path, monkeypatch):
    """PRESS PROOF 2: nothing in the store. The band states the posture,
    prints no tested zero, and the press still certifies."""
    pack = _pack()
    store_dir = tmp_path / "targets"
    store_dir.mkdir()

    press_mod = _press_fixture(monkeypatch, pack, store_dir)
    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=_fake_run, log=lambda m: None)

    assert result["certified"] is True, result["validation"]["violations"]
    html = Path(result["client_path"]).read_text(encoding="utf-8")
    band = _band(html)
    assert "TARGETING SUPPLY NOT RUN" in band
    assert "NO CONTACTS RESOLVED" not in band
    assert 'data-targeting-table="1"' in band
    csv_path = tmp_path / "acme_networks.golden_report.apollo_targets.csv"
    assert csv_path.exists()
    assert csv_path.read_text(encoding="utf-8").strip().count("\n") == 0


def test_a_replay_reproduces_band_09_without_reopening_the_store(
        tmp_path, monkeypatch):
    """Replay determinism: the sealed pack carries the contacts, so a
    re-render neither reads the store nor calls anything."""
    pack = _pack()
    store_dir = tmp_path / "targets"
    store_dir.mkdir()
    spec_ids = [s["spec_id"] for s in build_targeting(pack)["specs"]]
    (store_dir / "acme_networks.contacts.json").write_text(
        json.dumps(_stored(pack, [_contact(spec_ids[0])])), encoding="utf-8")

    press_mod = _press_fixture(monkeypatch, pack, store_dir)
    first = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=_fake_run, log=lambda m: None)
    original = Path(first["html_path"]).read_bytes()

    # remove the store entirely, then replay
    (store_dir / "acme_networks.contacts.json").unlink()
    replay = press_mod.rerender_golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path, log=lambda m: None)
    assert Path(replay["html_path"]).read_bytes() == original
    assert "Dana Reyes" in Path(replay["client_path"]).read_text(
        encoding="utf-8")
