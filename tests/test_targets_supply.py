"""The Apollo supply step and the durable targets store (amendment v1.4).

SWEEP CLASS, NEVER PRESS CLASS. The point of this step is that the press
never makes a live call: a separate runner resolves identities on the
operator's schedule and writes a file, and the press reads the file. These
tests pin that separation structurally (the press packages may not even
import the Apollo module), then pin every guard the step carries.

Every guard gets its negative case, because a supply step that fails quietly
is indistinguishable from one that ran and found nobody, and that is exactly
the distinction Band 09's zero-state law depends on:
  switch off  -> raises, writes nothing
  no key      -> raises, writes nothing
  API error   -> raises, writes nothing
  call cap    -> stops, keeps what it has, DISCLOSES the truncation
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import targets_store  # noqa: E402
from tools import apollo_targets  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
_NOW = datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)


def _spec(name="Internal Revenue Service", rule="T1", ordinal=1):
    return {
        "spec_id": f"{rule.lower()}-{ordinal:02d}-slug",
        "rule_id": rule, "row_class": "renewal", "source_rule": "R1",
        "buying_component": name, "office": None,
        "target_org": {"kind": "buying_component", "name": name},
        "seeks_tiers": [1, 2],
        "person_titles": ["Program Manager", "Contracting Officer"],
        "person_seniorities": ["director", "manager"],
        "persona_titles": {"1": ["Program Manager"],
                           "2": ["Contracting Officer"]},
        "rationale": "why this call",
        "join_record_ids": ["GS35F001"],
        "join_records": [{"record_id": "GS35F001",
                          "url": "https://www.usaspending.gov/award/X"}],
        "evidence": {}, "rule_basis": "basis",
    }


def _people(*names):
    return {"people": [{"id": f"id{i}", "name": n,
                        "title": "Contracting Officer",
                        "email": f"{n.split()[0].lower()}@irs.gov",
                        "organization": {"name": "Internal Revenue Service"}}
                       for i, n in enumerate(names)]}


@pytest.fixture()
def switched_on(monkeypatch):
    monkeypatch.setenv("LILA_ENABLE_APOLLO", "on")
    monkeypatch.setenv("APOLLO_API_KEY", "test-key-not-a-real-key")


# ---- the press may not even reach this module ------------------------------ #
def test_no_press_module_imports_the_apollo_supply_step():
    """STRUCTURAL PROOF of zero live calls at press time: if the render
    path cannot import the Apollo module, it cannot call Apollo, whatever a
    future edit does inside a band."""
    import re

    # IMPORT statements only. press.py legitimately NAMES the export file
    # (<stem>.apollo_targets.csv) without being able to call anything, and a
    # substring match would read that filename as a dependency.
    imports = re.compile(r"^\s*(?:from|import)\b.*\bapollo_targets\b", re.M)
    offenders = [path.name
                 for path in (REPO / "agents" / "golden_press").glob("*.py")
                 if imports.search(path.read_text(encoding="utf-8"))]
    assert offenders == [], (
        f"{offenders} import the Apollo supply step; the press reads the "
        f"targets store and calls nobody")


def test_the_store_read_is_the_only_people_lookup_a_press_performs(
        tmp_path, monkeypatch):
    """The press-side seam is a file read. With the network seam poisoned
    and no key in the environment, the store still answers."""
    monkeypatch.setenv(targets_store.STORE_DIR_ENV, str(tmp_path))
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.setattr(apollo_targets, "_http_post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a press touched the network")))
    payload = {"receipt": {"attempted": True, "retrieved_at": "2026-08-05",
                           "result_count": 1, "source": "apollo"},
               "contacts": []}
    targets_store.write("acme_networks", payload)
    assert targets_store.load("acme_networks")["receipt"]["attempted"] is True


# ---- the switch defaults off and refusal is loud --------------------------- #
def test_apollo_is_off_unless_the_operator_turns_it_on(monkeypatch):
    monkeypatch.delenv("LILA_ENABLE_APOLLO", raising=False)
    assert apollo_targets.apollo_enabled() is False
    monkeypatch.setenv("LILA_ENABLE_APOLLO", "on")
    assert apollo_targets.apollo_enabled() is True


def test_resolve_refuses_loudly_when_the_switch_is_off(monkeypatch):
    """NEGATIVE: an off switch must raise, never return an empty result that
    would read downstream as a tested zero."""
    monkeypatch.delenv("LILA_ENABLE_APOLLO", raising=False)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key-not-a-real-key")
    with pytest.raises(apollo_targets.ApolloError, match="LILA_ENABLE_APOLLO"):
        apollo_targets.resolve([_spec()], poster=lambda p: _people("X Y"))


def test_resolve_refuses_loudly_when_no_key_is_in_the_environment(monkeypatch):
    """NEGATIVE: a missing key raises. No code path here places one."""
    monkeypatch.setenv("LILA_ENABLE_APOLLO", "on")
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    with pytest.raises(apollo_targets.ApolloError, match="APOLLO_API_KEY"):
        apollo_targets.resolve([_spec()], poster=lambda p: _people("X Y"))


def test_resolve_propagates_an_api_error_instead_of_writing_a_zero(
        switched_on):
    """NEGATIVE: an API failure is loud. A partial write that looked like a
    completed screen is the failure mode this prevents."""
    def boom(payload):
        raise apollo_targets.ApolloError("Apollo returned HTTP 429")

    with pytest.raises(apollo_targets.ApolloError, match="429"):
        apollo_targets.resolve([_spec()], poster=boom)


def test_resolve_refuses_a_response_that_is_not_a_people_list(switched_on):
    """NEGATIVE: a shape change upstream must fail, not silently store
    nothing."""
    with pytest.raises(apollo_targets.ApolloError, match="people"):
        apollo_targets.resolve([_spec()],
                               poster=lambda p: {"people": "nope"})


# ---- what a successful run writes ------------------------------------------ #
def test_every_resolved_row_carries_apollo_provenance_and_the_date(
        switched_on):
    out = apollo_targets.resolve([_spec()], poster=lambda p: _people(
        "Dana Reyes", "Sam Cole"), now=_NOW)
    assert len(out["contacts"]) == 2
    for row in out["contacts"]:
        assert row["provenance"] == {
            "class": "apollo", "retrieved_at": "2026-08-05",
            "source": apollo_targets.SOURCE_NAME}
        assert row["spec_id"] == "t1-01-slug"
        assert row["join_record_ids"] == ["GS35F001"]
    # and the rows survive the store's own admission law
    kept, dropped = targets_store.admissible(out, spec_ids=["t1-01-slug"])
    # enrich() writes a screen block on every matched row, so these are
    # admissible without the fixture adding one
    assert len(kept) == 2, dropped


def test_a_locked_email_is_never_stored_as_an_address(switched_on):
    """Apollo returns a placeholder when the address sits behind a credit.
    Storing it would put a fake email in the export and in front of an
    operator."""
    locked = {"people": [{"id": "1", "name": "Dana Reyes",
                          "title": "Contracting Officer",
                          "email": "email_not_unlocked@domain.com"}]}
    out = apollo_targets.resolve([_spec()], poster=lambda p: locked, now=_NOW)
    assert "email" not in out["contacts"][0]
    assert out["receipt"]["emails_locked"] == 1
    assert "locked behind a credit" in out["receipt"]["spend_note"]


def test_a_person_with_no_name_is_not_a_contact(switched_on):
    out = apollo_targets.resolve(
        [_spec()], poster=lambda p: {"people": [{"id": "1", "title": "x"}]},
        now=_NOW)
    assert out["contacts"] == []
    assert out["receipt"]["result_count"] == 1


def test_the_search_payload_uses_the_specs_own_org_and_titles():
    payload = apollo_targets.search_payload(_spec())
    assert payload["q_organization_name"] == "Internal Revenue Service"
    assert payload["person_titles"] == ["Program Manager",
                                        "Contracting Officer"]


# ---- the cap is real and it is disclosed ----------------------------------- #
def test_the_call_cap_stops_the_run_and_says_so(switched_on):
    specs = [_spec(f"Component {i}", ordinal=i) for i in range(1, 4)]
    calls = []

    def poster(payload):
        calls.append(payload)
        return _people("Dana Reyes")

    out = apollo_targets.resolve(specs, poster=poster, call_cap=2, now=_NOW)
    receipt = out["receipt"]
    assert len(calls) == 2
    assert receipt["calls_made"] == 2 and receipt["call_cap"] == 2
    assert receipt["truncated"] is True
    assert "the 2-call cap stopped the run" in receipt["truncated_note"]
    assert receipt["specs_read"] == 3 and receipt["specs_searched"] == 2


def test_an_uncapped_run_never_claims_a_truncation(switched_on):
    """NEGATIVE for the cap: truncated is True only when something was
    actually cut, the one dialect every capped lane here speaks."""
    out = apollo_targets.resolve([_spec()], poster=lambda p: _people("A B"),
                                 call_cap=25, now=_NOW)
    assert out["receipt"]["truncated"] is False
    assert "truncated_note" not in out["receipt"]


def test_ten_specs_at_one_reseller_cost_one_call_not_ten(switched_on):
    """MEASURED 2026-08-05: adjacency routes ten apexanalytix forecast rows
    through one reseller. Paying ten credits for one answer would burn the
    cap on repeats, so identical searches collapse and the result is
    attributed back to every spec that asked."""
    specs = [_spec("DEV TECHNOLOGY GROUP INC", rule="T3", ordinal=i)
             for i in range(1, 11)]
    calls = []

    def poster(payload):
        calls.append(payload)
        return _people("Dana Reyes")

    out = apollo_targets.resolve(specs, poster=poster, now=_NOW)
    assert len(calls) == 1
    assert out["receipt"]["distinct_searches"] == 1
    assert out["receipt"]["specs_read"] == 10
    assert "10 spec(s) collapsed to 1 distinct search(es)" in \
        out["receipt"]["spend_note"]
    # one row for the person, naming every spec the search answered
    assert len(out["contacts"]) == 1
    assert len(out["contacts"][0]["spec_ids"]) == 10


def test_two_different_organisations_are_two_searches(switched_on):
    """NEGATIVE for the dedupe: it collapses IDENTICAL questions only."""
    specs = [_spec("Internal Revenue Service", ordinal=1),
             _spec("Social Security Administration", ordinal=2)]
    calls = []
    out = apollo_targets.resolve(
        specs, poster=lambda p: (calls.append(p), _people("A B"))[1],
        now=_NOW)
    assert len(calls) == 2 and out["receipt"]["distinct_searches"] == 2


def test_the_tier_follows_the_resolved_title_not_the_first_rung(switched_on):
    """A spec seeking tiers 1 and 2 searches both title sets at once, so a
    contracting officer who comes back is tier 2, not tier 1."""
    people = {"people": [
        {"id": "1", "name": "Dana Reyes", "title": "Contracting Officer"},
        {"id": "2", "name": "Sam Cole", "title": "Program Manager"}]}
    out = apollo_targets.resolve([_spec()], poster=lambda p: people, now=_NOW)
    by_name = {row["name"]: row["tier"] for row in out["contacts"]}
    assert by_name == {"Dana Reyes": 2, "Sam Cole": 1}


def test_the_receipt_prints_the_spend(switched_on):
    out = apollo_targets.resolve([_spec()], poster=lambda p: _people(
        "Dana Reyes", "Sam Cole"), now=_NOW)
    receipt = out["receipt"]
    assert receipt["attempted"] is True
    assert receipt["result_count"] == 2 and receipt["contacts_written"] == 2
    assert "1 search call(s) against a 25-call cap" in receipt["spend_note"]
    assert receipt["endpoint"] == apollo_targets.API_URL


# ---- a dry run costs nothing and needs no switch --------------------------- #
def test_a_dry_run_plans_the_calls_without_a_key_or_a_switch(monkeypatch):
    monkeypatch.delenv("LILA_ENABLE_APOLLO", raising=False)
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    plan = apollo_targets.dry_run([_spec(), _spec("Other", ordinal=2)])
    assert plan["attempted"] is False and plan["planned_calls"] == 2
    assert plan["searches"][0]["q_organization_name"] == \
        "Internal Revenue Service"


def test_a_dry_run_counts_the_calls_the_run_would_actually_make(monkeypatch):
    """The planned count is the DEDUPED one. Overstating it would have the
    operator budget for calls the step is not going to make."""
    monkeypatch.delenv("LILA_ENABLE_APOLLO", raising=False)
    specs = [_spec("DEV TECHNOLOGY GROUP INC", rule="T3", ordinal=i)
             for i in range(1, 11)]
    plan = apollo_targets.dry_run(specs)
    assert plan["specs_read"] == 10 and plan["planned_calls"] == 1
    assert len(plan["searches"]) == 1


# ---- the store -------------------------------------------------------------- #
def test_the_store_round_trips_through_its_atomic_writer(tmp_path,
                                                         monkeypatch):
    monkeypatch.setenv(targets_store.STORE_DIR_ENV, str(tmp_path))
    payload = {"receipt": {"attempted": True, "retrieved_at": "2026-08-05"},
               "contacts": [{"name": "Dana Reyes", "spec_id": "t1-01-slug",
                             "screen": {"state": "accepted", "verdict": "pass", "source_class": "federal_component", "findings": [], "cautions": []},
                             "provenance": {"class": "operator",
                                            "retrieved_at": "2026-08-05"}}]}
    path = targets_store.write("acme_networks", payload)
    assert path.name == "acme_networks.contacts.json"
    assert targets_store.load("acme_networks") == payload


@pytest.mark.parametrize("blob", ["not json", "[]", '{"contacts": []}',
                                  '{"receipt": {}, "contacts": "no"}'])
def test_an_unreadable_store_reads_as_never_run_not_as_a_tested_zero(
        tmp_path, monkeypatch, blob):
    """NEGATIVE for the store: None is the honest answer for 'no usable
    receipt', and Band 09 turns None into TARGETING SUPPLY NOT RUN rather
    than a zero it never earned."""
    monkeypatch.setenv(targets_store.STORE_DIR_ENV, str(tmp_path))
    (tmp_path / "acme_networks.contacts.json").write_text(blob,
                                                          encoding="utf-8")
    assert targets_store.load("acme_networks") is None


def test_a_missing_store_reads_as_never_run(tmp_path, monkeypatch):
    monkeypatch.setenv(targets_store.STORE_DIR_ENV, str(tmp_path))
    assert targets_store.load("nobody_here") is None


def test_a_contact_naming_no_spec_is_dropped_by_the_join_law():
    """NEGATIVE for the join law at the store: an identity with no spec has
    no federal record behind it and cannot render."""
    rows, dropped = targets_store.admissible(
        {"contacts": [{"name": "Dana Reyes", "spec_id": "gone",
                       "screen": {"state": "accepted", "verdict": "pass", "source_class": "federal_component", "findings": [], "cautions": []},
                       "provenance": {"class": "apollo",
                                      "retrieved_at": "2026-08-05"}}]},
        spec_ids=["t1-01-slug"])
    assert rows == [] and dropped["no_spec"] == 1


def test_a_field_level_provenance_that_cannot_be_read_strips_only_that_field():
    """The row survives with its readable fields; the unreadable one is
    stripped and counted rather than rendering unlabeled."""
    rows, dropped = targets_store.admissible({"contacts": [{
        "name": "Dana Reyes", "spec_id": "t1-01-slug",
        "title": "Contracting Officer", "email": "dana@irs.gov",
        "screen": {"state": "accepted", "verdict": "pass", "source_class": "federal_component", "findings": [], "cautions": []},
        "provenance": {"class": "apollo", "retrieved_at": "2026-08-05"},
        "field_provenance": {"email": {"class": "hearsay"}},
    }]}, spec_ids=["t1-01-slug"])
    assert len(rows) == 1
    assert "email" not in rows[0] and rows[0]["title"] == "Contracting Officer"
    assert dropped["fields_stripped"] == 1


def test_an_operator_corrected_field_keeps_its_own_provenance():
    rows, _ = targets_store.admissible({"contacts": [{
        "name": "Dana Reyes", "spec_id": "t1-01-slug",
        "email": "dana.reyes@irs.gov",
        "screen": {"state": "accepted", "verdict": "pass", "source_class": "federal_component", "findings": [], "cautions": []},
        "provenance": {"class": "apollo", "retrieved_at": "2026-08-05"},
        "field_provenance": {"email": {"class": "operator",
                                       "retrieved_at": "2026-08-06"}},
    }]}, spec_ids=["t1-01-slug"])
    assert targets_store.field_provenance(rows[0], "email")["class"] == \
        "operator"
    assert targets_store.field_provenance(rows[0], "name")["class"] == "apollo"


# ---- the runner honours the operator's Target door -------------------------- #
def _runner():
    import importlib
    return importlib.import_module("run_targets")


def test_the_runner_refuses_while_the_target_door_is_closed(
        tmp_path, monkeypatch, capsys, switched_on):
    """CONTRACT SURFACE (Assess -> Target operator door): outreach, Apollo
    named, is locked behind an explicit operator decision. This step spends
    credits on real people, so it sits behind that door and never writes
    it."""
    runner = _runner()
    monkeypatch.setattr(runner, "load_specs",
                        lambda *a, **k: ([_spec()], None))
    import agents.review as review_mod
    monkeypatch.setattr(review_mod, "target_gate_status",
                        lambda c, **k: (False, ["Target is locked"]))
    monkeypatch.setattr(sys, "argv",
                        ["run_targets.py", "--client", "Acme Networks"])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert "Target is locked" in str(exc.value)


def test_the_runner_dry_run_plans_without_touching_the_gate_or_the_api(
        monkeypatch, capsys):
    runner = _runner()
    monkeypatch.setattr(runner, "load_specs",
                        lambda *a, **k: ([_spec()], None))

    def _forbidden(*a, **k):
        raise AssertionError("a dry run called something")

    monkeypatch.setattr(apollo_targets, "_http_post", _forbidden)
    monkeypatch.setattr(sys, "argv", ["run_targets.py", "--client",
                                      "Acme Networks", "--dry-run"])
    assert runner.main() == 0
    printed = capsys.readouterr().out
    assert "DRY RUN" in printed and "Nothing was called" in printed


def test_the_runner_writes_the_store_and_prints_the_spend(
        tmp_path, monkeypatch, capsys, switched_on):
    runner = _runner()
    monkeypatch.setenv(targets_store.STORE_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(runner, "load_specs",
                        lambda *a, **k: ([_spec()], None))
    import agents.review as review_mod
    monkeypatch.setattr(review_mod, "target_gate_status",
                        lambda c, **k: (True, []))
    # The runner now walks THREE endpoints through the one seam: the free
    # search, the zero-cost balance read, and the billable work-email match.
    # A stub that answered them all with a search response would hide which
    # one was called, so it answers by URL.
    def _by_url(url, payload, key, **kw):
        if url == apollo_targets.API_URL:
            return _people("Dana Reyes")
        if url == apollo_targets.PROFILE_URL:
            return {"num_credits_remaining": 3889}
        if url == apollo_targets.BULK_MATCH_URL:
            assert payload["reveal_phone_number"] is False, \
                "the runner must never request a dial reveal"
            return {"matches": [{"id": "id0", "email": "dana@irs.gov",
                                 "email_status": "verified",
                                 "organization": {
                                     "phone": "+1 202-324-3000"}}]}
        raise AssertionError(f"unexpected endpoint {url}")

    monkeypatch.setattr(apollo_targets, "_http_post", _by_url)
    monkeypatch.setattr(sys, "argv", ["run_targets.py", "--client",
                                      "Acme Networks"])
    assert runner.main() == 0
    printed = capsys.readouterr().out
    assert "[out:targets-store]" in printed
    assert "search call(s) against a 25-call cap" in printed
    # the confirmed email path landed, and the spend was measured
    assert "PRE-FLIGHT COST ESTIMATE" in printed
    assert "emails resolved: 1" in printed
    assert "credits: projected 1 · actual 0 · balance 3889 -> 3889" in printed
    stored = json.loads(
        (tmp_path / "acme_networks.contacts.json").read_text(encoding="utf-8"))
    assert stored["contacts"][0]["name"] == "Dana Reyes"
    assert stored["receipt"]["attempted"] is True


def test_the_runner_reports_a_refusal_as_a_nonzero_exit(
        tmp_path, monkeypatch, capsys):
    """NEGATIVE at the runner: an off switch exits 2 with the reason, so a
    scheduled run cannot look successful."""
    runner = _runner()
    monkeypatch.delenv("LILA_ENABLE_APOLLO", raising=False)
    monkeypatch.setattr(runner, "load_specs",
                        lambda *a, **k: ([_spec()], None))
    import agents.review as review_mod
    monkeypatch.setattr(review_mod, "target_gate_status",
                        lambda c, **k: (True, []))
    monkeypatch.setattr(sys, "argv", ["run_targets.py", "--client",
                                      "Acme Networks"])
    assert runner.main() == 2
    assert "REFUSED" in capsys.readouterr().err
