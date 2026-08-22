"""Developing Horizon — fact bank, compose/refine validation, render, lints.
No LLM, no network: engines are faked, results dicts are fixtures."""

import ast
import json
from datetime import datetime
from pathlib import Path

import pytest

from agents.assess.contracts import LifecycleStage
from agents.reports.capture_brief import _horizon_section
from agents.reports.horizon import (
    HORIZON_SCHEMA_VERSION, HorizonItem, HorizonSet, HorizonSignal,
    HorizonValidationError,
    approve_horizon, approved_set, build_fact_bank, compose_horizon,
    current_horizon_binding, horizon_for_report, new_draft, refine_horizon,
    save_horizon, validate_horizon,
)
from agents.reports.lint import lint_horizon

NOTICE_URL = "https://sam.gov/opp/abc123/view"
NOTICE_TEXT = ('Special Notice: "Jeppesen Flight Charts" · DHS / CBP · '
               'response 2026-07-15')
AWARD_URL = "https://www.usaspending.gov/award/CONT_AWD_X1"
REG_URL = "https://www.federalregister.gov/d/2026-12345"
CONGRESS_URL = "https://www.congress.gov/bill/119th-congress/house-bill/1234"
DOD_URL = "https://www.defense.gov/News/Contracts/Contract/Article/4000000/"
APPROVED_AT = "2026-07-10T12:00:00+00:00"
BINDING = {
    "version": 1,
    "scope_designator": "all",
    "sweep_artifact": "searches_osprey.json",
    "sweep_sha256": "sweep-a",
    "profile_sha256": "profile-a",
}


def _results():
    return {
        "sam.gov": [
            {"source_id": "n1", "title": "Jeppesen Flight Charts",
             "agency": "DHS / CBP", "response_deadline": "2026-07-15",
             "raw_payload": {"type": "Special Notice", "url": NOTICE_URL}},
            {"source_id": "n2", "title": "Unrelated Facilities Mowing",
             "agency": "USDA", "raw_payload": {"type": "Solicitation",
                                               "url": "https://sam.gov/opp/zzz/view"}},
        ],
        "triage": {"n1": {"verdict": "monitor", "reason": "right buyer, wrong product"},
                   "n2": {"verdict": "discard", "reason": "off-mission"}},
        "expiring_awards": {"rows": [
            {"recipient": "GDIT", "awarding_agency": "DoD",
             "awarding_sub_agency": "Air Force", "amount": 5_000_000.0,
             "end_date": "2026-08-31", "naics": "541990", "url": AWARD_URL},
        ]},
        "forecast_signals": {"total_records": 745, "matched": []},
        "federal_register": {"airspace threat": [
            {"title": "Counter-UAS Authority Expansion", "type": "Rule",
             "publication_date": "2026-07-06", "html_url": REG_URL},
        ]},
    }


def _item(**over):
    base = dict(
        id="cbp-amo", title="CBP AMO subscription window", where="CBP AMO",
        verified=[HorizonSignal(evidence_id="H1", text=NOTICE_TEXT,
                                source=NOTICE_URL)],
        pattern="Repeated sole-source aviation-data buys from one office.",
        projection="We judge a subscription window forms at CBP AMO.",
        window="next 6-12 months",
        watching="new CBP AMO posting in these lanes; reviewed weekly",
        confidence="strong", lifecycle_stage=LifecycleStage.EARLY_SIGNAL,
        falsifier="No related planning or notice activity appears before review.",
        watch_trigger="new CBP AMO posting in these lanes",
        monitoring_cadence="reviewed weekly")
    base.update(over)
    return HorizonItem(**base)


def _set(items):
    return HorizonSet(client_name="Osprey", items=items,
                      method_note="Screened 745 planning records this cycle.")


def _v1_payload(items=None):
    """A bound pre-v2 artifact: old fields and old fact-bank shape only."""
    bank = build_fact_bank(_results())
    for fact in bank:
        fact.pop("retrieved_at", None)
    data = json.loads(_set(items if items is not None else [_item()]).model_dump_json())
    for item in data["items"]:
        for field in ("lifecycle_stage", "falsifier", "watch_trigger",
                      "monitoring_cadence"):
            item.pop(field, None)
        for signal in item["verified"]:
            signal.pop("evidence_id", None)
    return {
        "client": "Osprey", "status": "draft",
        "generated_at": "2026-07-10T12:00:00", "fact_bank": bank,
        "set": data, "rounds": [], "binding": dict(BINDING),
    }


# ── fact bank ────────────────────────────────────────────────────────────────

def test_fact_bank_is_cited_and_selective():
    bank = build_fact_bank(_results())
    sources = {f["source"] for f in bank}
    assert NOTICE_URL in sources          # monitor notice: in
    assert AWARD_URL in sources           # recompete row: in
    assert REG_URL in sources             # regulatory event: in
    assert "https://sam.gov/opp/zzz/view" not in sources  # discard verdict: OUT
    kinds = {f["kind"] for f in bank}
    assert {"sam_monitor", "expiring_award", "forecast_screen",
            "regulatory"} <= kinds
    # absence is retained as coverage context, not affirmative thesis evidence
    screen = next(f for f in bank if f["kind"] == "forecast_screen")
    assert "745" in screen["text"]
    assert "0 matched the client's capability vocabulary" in screen["text"]
    monitor = next(f for f in bank if f["kind"] == "sam_monitor")
    assert monitor["text"] == NOTICE_TEXT
    assert "triage" not in monitor["text"].lower()
    assert "right buyer, wrong product" not in monitor["text"]
    assert all(f["source"] for f in bank)  # no uncited entries, ever
    assert all(isinstance(f.get("scope"), dict) for f in bank)
    assert monitor["scope"]["kind"] == "agency"
    assert monitor["scope"]["agencies"] == ["DHS / CBP"]
    assert next(f for f in bank if f["kind"] == "regulatory")["scope"] == {
        "kind": "government_wide",
    }


def test_fact_bank_reads_canonical_congress_key_and_legacy_fallback():
    bill = {
        "bill": "H.R. 1234", "title": "Counter-UAS Modernization Act",
        "money_vehicle": "authorization", "action_date": "2026-07-09",
        "latest_action": "Referred to the Committee on Homeland Security",
        "matched": ["counter-UAS"], "url": CONGRESS_URL,
    }
    canonical = _results()
    canonical["congress"] = {"items": [bill]}
    current_fact = next(
        row for row in build_fact_bank(canonical) if row["kind"] == "legislation")
    assert current_fact["source"] == CONGRESS_URL
    assert "H.R. 1234" in current_fact["text"]

    legacy = _results()
    legacy["congress_gov"] = {"items": [bill]}
    legacy_fact = next(
        row for row in build_fact_bank(legacy) if row["kind"] == "legislation")
    assert (legacy_fact["text"], legacy_fact["source"]) == (
        current_fact["text"], current_fact["source"])

    # Canonical presence wins even when it is an explicit empty result. A
    # mixed artifact must not resurrect stale evidence under the legacy key.
    mixed = _results()
    mixed["congress"] = {"items": []}
    mixed["congress_gov"] = {"items": [bill]}
    assert not any(row["kind"] == "legislation"
                   for row in build_fact_bank(mixed))


def test_fact_bank_includes_dod_contract_wire_as_an_actual_input():
    results = _results()
    results["dod_contracts"] = {"items": [{
        "published": "2026-07-10", "title": "Air Force network award",
        "body": "Packet-capture modernization and cyber operations support.",
        "matched": ["packet capture"], "url": DOD_URL,
    }]}
    fact = next(row for row in build_fact_bank(results)
                if row["kind"] == "dod_award_wire")
    assert fact["source"] == DOD_URL
    assert "packet capture" in fact["text"].lower()


# ── validation: the no-fabrication wall ──────────────────────────────────────

def test_validate_rejects_foreign_source_and_count_claims():
    bank = build_fact_bank(_results())
    ok = validate_horizon(_set([_item()]), bank)
    assert ok == []
    # a source outside the bank is fabrication, full stop
    bad_src = _item(verified=[HorizonSignal(text="x", source="https://evil.example/made-up")])
    probs = validate_horizon(_set([bad_src]), bank)
    assert probs and "outside the fact bank" in probs[0]
    # an allowed URL cannot launder invented prose into a verified signal
    bad_text = _item(verified=[HorizonSignal(
        text="CBP announced a procurement that is not in the source.",
        source=NOTICE_URL)])
    probs = validate_horizon(_set([bad_text]), bank)
    assert probs and "does not exactly match" in probs[0]
    # freewritten counts against machine-reconciled nouns fail
    bad_count = _item(pattern="Three sole-source notices from one office.")
    probs = validate_horizon(_set([bad_count]), bank)
    assert probs and "count claim" in probs[0]
    # ...but safe nouns pass ('buys' is not a reconciled noun)
    good = _item(pattern="Three sole-source buys from one office.")
    assert validate_horizon(_set([good]), bank) == []


def test_forecast_screen_cannot_support_a_thesis_by_itself():
    bank = build_fact_bank(_results())
    screen = next(f for f in bank if f["kind"] == "forecast_screen")
    coverage = HorizonSignal(text=screen["text"], source=screen["source"])
    only_coverage = _item(verified=[coverage])
    problems = validate_horizon(_set([only_coverage]), bank)
    assert problems and "coverage or absence cannot support" in problems[0]

    # Coverage may explain the screen when a canonical affirmative fact also
    # supports the item.
    with_evidence = _item(verified=[coverage, HorizonSignal(
        text=NOTICE_TEXT, source=NOTICE_URL)])
    assert validate_horizon(_set([with_evidence]), bank) == []


def test_zero_item_horizon_is_valid_and_empty_bank_skips_the_engine():
    empty = _set([])
    assert validate_horizon(empty, build_fact_bank(_results())) == []

    calls = []

    class ZeroEngine:
        def deliberate(self, **kwargs):
            calls.append(kwargs)
            return _set([])

    hset, _, problems = compose_horizon("Osprey", _results(), engine=ZeroEngine())
    assert problems == [] and hset.items == [] and len(calls) == 1

    class MustNotRun:
        def deliberate(self, **kwargs):
            raise AssertionError("empty fact bank must not invoke an LLM")

    hset, bank, problems = compose_horizon("Osprey", {}, engine=MustNotRun())
    assert bank == [] and problems == [] and hset.items == []


def test_new_draft_is_v2_with_ledger_ready_fields_and_one_bank_timestamp():
    bank = build_fact_bank(_results())
    retrieved = {f.get("retrieved_at") for f in bank}
    assert len(retrieved) == 1
    datetime.fromisoformat(next(iter(retrieved)))

    item = _item(watching="composer copy is not authoritative")
    payload = new_draft("Osprey", _set([item]), bank, binding=BINDING)
    stored = payload["set"]["items"][0]
    assert payload["schema_version"] == HORIZON_SCHEMA_VERSION == 2
    assert stored["verified"][0]["evidence_id"] == "H1"
    assert stored["lifecycle_stage"] == "early_signal"
    assert stored["falsifier"]
    assert stored["watching"] == (
        stored["watch_trigger"] + "; " + stored["monitoring_cadence"])


@pytest.mark.parametrize("missing,expected", [
    ("evidence_id", "requires evidence_id"),
    ("lifecycle_stage", "requires lifecycle_stage"),
    ("falsifier", "requires falsifier"),
    ("watch_trigger", "requires watch_trigger"),
    ("monitoring_cadence", "requires monitoring_cadence"),
    ("retrieved_at", "has no retrieved_at"),
])
def test_v2_nonempty_draft_requires_every_ledger_field(missing, expected):
    payload = new_draft(
        "Osprey", _set([_item()]), build_fact_bank(_results()), binding=BINDING)
    if missing == "evidence_id":
        payload["set"]["items"][0]["verified"][0].pop(missing)
    elif missing == "retrieved_at":
        payload["fact_bank"][0].pop(missing)
    else:
        payload["set"]["items"][0].pop(missing)
    with pytest.raises(HorizonValidationError, match=expected):
        approve_horizon(payload, current_binding=BINDING)


def test_v2_evidence_id_must_resolve_the_exact_text_and_source():
    payload = new_draft(
        "Osprey", _set([_item()]), build_fact_bank(_results()), binding=BINDING)
    payload["set"]["items"][0]["verified"][0]["evidence_id"] = "H2"
    with pytest.raises(HorizonValidationError, match="does not exactly match"):
        approve_horizon(payload, current_binding=BINDING)


@pytest.mark.parametrize("failure,expected", [
    ("sam_wrong_host", "SAM monitor source must resolve to SAM.gov"),
    ("government_nonofficial", "HTTPS .gov or .mil"),
    ("government_http", "source must use HTTPS"),
    ("naive_retrieval", "retrieved_at must be timezone-aware"),
])
def test_approval_and_report_reject_unofficial_sources_or_naive_time(
        failure, expected, tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    payload = new_draft(
        "Osprey", _set([_item()]), build_fact_bank(_results()), binding=BINDING)
    fact = payload["fact_bank"][0]
    signal = payload["set"]["items"][0]["verified"][0]
    if failure == "sam_wrong_host":
        fact["source"] = signal["source"] = AWARD_URL
    elif failure == "government_nonofficial":
        fact["kind"] = "legislation"
        fact["source"] = signal["source"] = "https://example.com/bill/1234"
    elif failure == "government_http":
        fact["kind"] = "legislation"
        fact["source"] = signal["source"] = CONGRESS_URL.replace(
            "https://", "http://")
    else:
        fact["retrieved_at"] = "2026-07-10T12:00:00"

    with pytest.raises(HorizonValidationError, match=expected):
        approve_horizon(payload, current_binding=BINDING)

    # The report loader reuses the same deterministic wall; an artifact cannot
    # bypass it merely by carrying an "approved" status string on disk.
    payload["status"] = "approved"
    payload["approved_at"] = APPROVED_AT
    payload["approved_by"] = "operator"
    save_horizon(payload)
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid"
    assert any(expected in problem for problem in problems)


def test_approval_and_report_reject_legacy_sam_triage_laundering(
        tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    payload = new_draft(
        "Osprey", _set([_item()]), build_fact_bank(_results()), binding=BINDING)
    laundered = NOTICE_TEXT + " · triage: right buyer, wrong product"
    payload["fact_bank"][0]["text"] = laundered
    payload["set"]["items"][0]["verified"][0]["text"] = laundered

    with pytest.raises(HorizonValidationError,
                       match="SAM monitor includes non-source triage analysis"):
        approve_horizon(payload, current_binding=BINDING)

    payload["status"] = "approved"
    payload["approved_at"] = APPROVED_AT
    payload["approved_by"] = "operator"
    save_horizon(payload)
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid"
    assert any("non-source triage analysis" in problem for problem in problems)


def test_draft_approval_and_report_reject_inner_client_crossover(
        tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    bank = build_fact_bank(_results())
    foreign_set = _set([_item()])
    foreign_set.client_name = "OtherCo"
    with pytest.raises(HorizonValidationError,
                       match="client_name does not match artifact client"):
        new_draft("Osprey", foreign_set, bank, binding=BINDING)

    payload = new_draft(
        "Osprey", _set([_item()]), bank, binding=BINDING)
    payload["set"]["client_name"] = "OtherCo"
    with pytest.raises(HorizonValidationError,
                       match="client_name does not match artifact client"):
        approve_horizon(payload, current_binding=BINDING)

    payload["status"] = "approved"
    payload["approved_at"] = APPROVED_AT
    payload["approved_by"] = "operator"
    save_horizon(payload)
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid"
    assert any("client_name does not match artifact client" in problem
               for problem in problems)


@pytest.mark.parametrize("failure,expected", [
    ("blank", "item at index 0 has no id"),
    ("duplicate", "item id 'cbp-amo' is duplicated"),
])
def test_approval_and_report_reject_blank_or_duplicate_item_ids(
        failure, expected, tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    payload = new_draft(
        "Osprey", _set([_item()]), build_fact_bank(_results()), binding=BINDING)
    if failure == "blank":
        payload["set"]["items"][0]["id"] = "   "
    else:
        duplicate = json.loads(json.dumps(payload["set"]["items"][0]))
        payload["set"]["items"].append(duplicate)

    with pytest.raises(HorizonValidationError, match=expected):
        approve_horizon(payload, current_binding=BINDING)

    payload["status"] = "approved"
    payload["approved_at"] = APPROVED_AT
    payload["approved_by"] = "operator"
    save_horizon(payload)
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid"
    assert any(expected in problem for problem in problems)


def test_v1_remains_readable_renderable_and_is_not_silently_upgraded(
        tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    payload = _v1_payload()
    approved = approve_horizon(payload, current_binding=BINDING)
    assert "schema_version" not in approved
    assert "evidence_id" not in approved["set"]["items"][0]["verified"][0]
    assert "lifecycle_stage" not in approved["set"]["items"][0]
    save_horizon(approved)
    hset, status, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert status == "approved" and problems == [] and hset["items"]

    class V2ShapedRefine:
        def deliberate(self, **_kwargs):
            return _set([_item(projection="We judge the v1 wording is sharper.")])

    refined = refine_horizon(
        approved, "sharpen", engine=V2ShapedRefine(), current_binding=BINDING)
    stored = refined["set"]["items"][0]
    assert "schema_version" not in refined
    assert not any(k in stored for k in (
        "lifecycle_stage", "falsifier", "watch_trigger",
        "monitoring_cadence"))
    assert "evidence_id" not in stored["verified"][0]


def test_bound_zero_item_v2_does_not_require_fact_metadata():
    payload = new_draft("Osprey", _set([]), [], binding=BINDING)
    approved = approve_horizon(payload, current_binding=BINDING)
    assert approved["status"] == "approved"
    assert approved["approved_at"] and approved["approved_by"] == "operator"
    assert approved["set"]["items"] == []


def test_approved_status_without_human_metadata_never_renders(
        tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    payload = new_draft(
        "Osprey", _set([]), [], binding=BINDING)
    payload["status"] = "approved"
    save_horizon(payload)
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid"
    assert any("approval timestamp" in problem for problem in problems)

    payload["status"] = "draft"
    payload["approved_at"] = APPROVED_AT
    payload["approved_by"] = "operator"
    with pytest.raises(HorizonValidationError,
                       match="draft Horizon cannot carry approval metadata"):
        approve_horizon(payload, current_binding=BINDING)


def test_validate_blocks_citing_the_client_back_to_themselves():
    """The client's footprint is INTERNAL reasoning fuel: a signal citing a
    client_known source, or item text restating their own award id, is blocked
    as hard as fabrication — the map tells the client only what they DON'T
    know."""
    results = dict(_results())
    results["client_awards"] = {"rows": [
        {"award_id": "70T02023C7554N002", "amount": 994455.0,
         "awarding_agency": "DHS", "awarding_sub_agency": "TSA",
         "start_date": "2023-07-14", "end_date": "2025-07-13",
         "url": "https://www.usaspending.gov/award/CONT_AWD_TSA1"}]}
    bank = build_fact_bank(results)
    known = next(f for f in bank if f["kind"] == "client_past_performance")
    assert known["client_known"] is True and "INTERNAL" in known["text"]

    # citing the client's own award as a signal -> blocked
    self_cite = _item(verified=[HorizonSignal(
        text="client TSA contract ended 2025-07-13",
        source="https://www.usaspending.gov/award/CONT_AWD_TSA1")])
    probs = validate_horizon(_set([self_cite]), bank)
    assert probs and "cites the client back to themselves" in probs[0]

    # restating their award id in prose -> blocked
    tell = _item(pattern="Your award 70T02023C7554N002 is on a renewal clock.")
    probs = validate_horizon(_set([tell]), bank)
    assert probs and "restates the client's own award" in probs[0]

    # market-facing items citing market sources still pass
    assert validate_horizon(_set([_item()]), bank) == []


def test_compose_retries_once_with_violations_then_reports():
    bank_results = _results()
    calls = []

    class FakeEngine:
        def deliberate(self, *, layer, system_prompt, context, schema):
            calls.append(context)
            if len(calls) == 1:   # first pass cites a made-up source
                return _set([_item(verified=[
                    HorizonSignal(text="x", source="https://invented.example")])])
            return _set([_item()])  # retry passes

    hset, bank, problems = compose_horizon("Osprey", bank_results, engine=FakeEngine())
    assert problems == []
    assert len(calls) == 2
    assert "VALIDATION FAILED" in calls[1]["instruction"]
    assert hset.items[0].verified[0].source == NOTICE_URL


def test_new_composition_rejects_a_nonempty_v1_shaped_response():
    class LegacyShapedEngine:
        def deliberate(self, **_kwargs):
            item = _item()
            item.verified[0].evidence_id = None
            item.lifecycle_stage = None
            item.falsifier = None
            item.watch_trigger = None
            item.monitoring_cadence = None
            return _set([item])

    _hset, _bank, problems = compose_horizon(
        "Osprey", _results(), engine=LegacyShapedEngine())
    assert any("requires evidence_id" in p for p in problems)
    for field in ("lifecycle_stage", "falsifier", "watch_trigger",
                  "monitoring_cadence"):
        assert any(f"requires {field}" in p for p in problems)


# ── refine: the dialogue gate ────────────────────────────────────────────────

def test_refine_applies_and_rejects(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    bank = build_fact_bank(_results())
    payload = new_draft("Osprey", _set([_item()]), bank, binding=BINDING)

    class Sharpen:
        def deliberate(self, *, layer, system_prompt, context, schema):
            assert layer == "horizon-refine"
            assert context["instruction"].startswith("sharpen")
            s = _set([_item(projection="We judge the window is imminent.")])
            s.refine_note = ""
            return s

    payload = approve_horizon(payload, current_binding=BINDING)
    payload = refine_horizon(payload, "sharpen the projection", engine=Sharpen(),
                             current_binding=BINDING)
    assert payload["set"]["items"][0]["projection"] == "We judge the window is imminent."
    assert payload["rounds"][-1]["applied"] is True
    # ...an applied edit REOPENS the gate: what renders is only what a human
    # last approved, so the refine knocks approved back to draft
    assert payload["status"] == "draft" and "approved_at" not in payload

    class Fabricator:  # tries to invent a source both times
        def deliberate(self, *, layer, system_prompt, context, schema):
            return _set([_item(verified=[
                HorizonSignal(text="x", source="https://invented.example")])])

    before = json.dumps(payload["set"])
    payload = refine_horizon(payload, "add a bigger dollar figure",
                             engine=Fabricator(), current_binding=BINDING)
    assert json.dumps(payload["set"]) == before          # prior set survives
    assert payload["rounds"][-1]["applied"] is False
    assert "REJECTED" in payload["rounds"][-1]["note"]


def test_only_approved_sets_render(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    bank = build_fact_bank(_results())
    payload = new_draft("Osprey", _set([_item()]), bank, binding=BINDING)
    save_horizon(payload)
    assert approved_set("Osprey", current_binding=BINDING) is None
    save_horizon(approve_horizon(payload, current_binding=BINDING))
    got = approved_set("Osprey", current_binding=BINDING)
    assert got and got["items"][0]["id"] == "cbp-amo"    # approved: renders


def test_approval_revalidates_without_mutating_a_bad_draft():
    bank = build_fact_bank(_results())
    payload = new_draft("Osprey", _set([_item()]), bank, binding=BINDING)
    payload["set"]["items"][0]["verified"][0]["text"] = "invented at valid URL"
    with pytest.raises(HorizonValidationError) as caught:
        approve_horizon(payload, current_binding=BINDING)
    assert "does not exactly match" in str(caught.value)
    assert payload["status"] == "draft" and "approved_at" not in payload


def test_report_loader_degrades_every_nonapproved_or_invalid_state(tmp_path,
                                                                  monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    assert horizon_for_report("Osprey", current_binding=BINDING) == (
        None, "missing", [])

    bank = build_fact_bank(_results())
    payload = new_draft("Osprey", _set([_item()]), bank, binding=BINDING)
    save_horizon(payload)
    assert horizon_for_report("Osprey", current_binding=BINDING) == (
        None, "draft", [])

    save_horizon(approve_horizon(payload, current_binding=BINDING))
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert state == "approved" and problems == [] and hset["items"]

    zero = new_draft("Osprey", _set([]), bank, binding=BINDING)
    save_horizon(approve_horizon(zero, current_binding=BINDING))
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert state == "approved" and problems == [] and hset["items"] == []

    zero["set"]["items"] = [json.loads(_item().model_dump_json())]
    zero["set"]["items"][0]["verified"][0]["text"] = "fabricated"
    save_horizon(zero)
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid"
    assert any("does not exactly match" in p for p in problems)

    # A corrupt artifact is also a diagnostic omission, never an exception.
    (tmp_path / "osprey.horizon.json").write_text("{", encoding="utf-8")
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid" and problems


def test_legacy_unbound_horizon_can_be_read_but_never_approved_or_rendered(
        tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    bank = build_fact_bank(_results())
    legacy = new_draft("Osprey", _set([_item()]), bank)
    legacy["status"] = "approved"  # legacy auto-approved artifact on disk
    legacy["approved_at"] = APPROVED_AT
    legacy["approved_by"] = "operator"
    save_horizon(legacy)

    with pytest.raises(HorizonValidationError, match="legacy/unbound"):
        approve_horizon(legacy, current_binding=BINDING)
    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=BINDING)
    assert hset is None and state == "invalid"
    assert any("legacy/unbound" in p for p in problems)


@pytest.mark.parametrize("changed_key,new_value", [
    ("scope_designator", "agency_dhs"),
    ("sweep_artifact", "searches_osprey.agency_dhs.json"),
    ("sweep_sha256", "sweep-b"),
    ("profile_sha256", "profile-b"),
])
def test_scope_sweep_or_profile_change_reopens_horizon_gate(
        changed_key, new_value, tmp_path, monkeypatch):
    monkeypatch.setattr("agents.reports.horizon._REVIEW_DIR", tmp_path)
    bank = build_fact_bank(_results())
    payload = new_draft("Osprey", _set([_item()]), bank, binding=BINDING)
    payload = approve_horizon(payload, current_binding=BINDING)
    save_horizon(payload)
    changed = {**BINDING, changed_key: new_value}

    hset, state, problems = horizon_for_report(
        "Osprey", current_binding=changed)
    assert hset is None and state == "invalid" and problems
    with pytest.raises(HorizonValidationError, match="changed"):
        approve_horizon(payload, current_binding=changed)

    class MustNotRefine:
        def deliberate(self, **kwargs):
            raise AssertionError("stale Horizon must fail before an LLM call")

    with pytest.raises(HorizonValidationError, match="changed"):
        refine_horizon(payload, "edit stale evidence", engine=MustNotRefine(),
                       current_binding=changed)


def test_current_binding_hashes_exact_gate_sweep_and_profile(tmp_path,
                                                            monkeypatch):
    sweep = tmp_path / "searches_osprey.agency_dhs.json"
    sweep.write_text(json.dumps({"results": _results()}), encoding="utf-8")
    monkeypatch.setattr("agents.review.gate_designator",
                        lambda _client: "agency_dhs")
    profile = {"client_name": "Osprey", "capability_terms": ["aviation"]}

    first = current_horizon_binding("Osprey", sweep_path=str(sweep),
                                    profile=profile)
    assert first["scope_designator"] == "agency_dhs"
    assert first["sweep_artifact"] == sweep.name

    sweep.write_text(json.dumps({"results": {**_results(), "new": 1}}),
                     encoding="utf-8")
    changed_sweep = current_horizon_binding(
        "Osprey", sweep_path=str(sweep), profile=profile)
    changed_profile = current_horizon_binding(
        "Osprey", sweep_path=str(sweep), profile={**profile, "new": 1})
    assert changed_sweep["sweep_sha256"] != first["sweep_sha256"]
    assert changed_profile["profile_sha256"] != first["profile_sha256"]


def test_assessment_entry_point_cannot_compose_or_approve_horizon():
    """The base report path may load an approved layer, never create one."""
    source = (Path(__file__).resolve().parents[1] / "run_capture_brief.py").read_text()
    tree = ast.parse(source)
    forbidden = {"compose_horizon", "approve_horizon"}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not (forbidden & used)


# ── render + lint ────────────────────────────────────────────────────────────

def test_render_shows_work_and_passes_lint():
    hz = json.loads(_set([_item()]).model_dump_json())
    html = _horizon_section(hz)
    assert "GTM Group projection" in html
    assert NOTICE_URL in html
    assert 'data-horizon="1"' in html
    assert "STRONG SIGNAL" in html
    assert "Where the Next" in html
    page = f"<html><body>{html}</body></html>"
    assert lint_horizon(page).ok
    assert _horizon_section(None) == ""                  # no items, no section
    assert _horizon_section({"items": []}) == ""


def test_lint_horizon_catches_stray_uncited_and_unlabeled():
    # a horizon item outside the section = misrepresentation
    stray = '<html><body><div data-horizon="1">loose projection</div></body></html>'
    r = lint_horizon(stray)
    assert not r.ok and r.violations[0].rule == "horizon_outside_section"
    # inside the section but uncited / unlabeled = unshown work
    bare = ('<html><body><section class="developing-horizon">'
            '<div data-horizon="1"><p>vibes only</p></div>'
            "</section></body></html>")
    rules = {v.rule for v in lint_horizon(bare).violations}
    assert rules == {"horizon_projection_unlabeled", "horizon_item_uncited"}
