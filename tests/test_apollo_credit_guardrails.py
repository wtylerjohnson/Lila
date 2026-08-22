"""Credit guardrails, and the phone policy they exist to protect (Tasks 2+5).

THE BURN THIS ENCODES. On 2026-08-06 a phone reveal fired for two people
because the tool that retrieves the result was PRESENT in the caller's tool
list. Present was not authorised: every retrieval refused, and 16 direct-dial
credits bought numbers that could never be fetched. The account ledger
recorded it as "Phone number revealed, Mobile Number, 2 Contacts, 16".

So the guardrail is not a docstring. A billable call is UNREACHABLE until
its own result path has answered a zero-cost probe in the same run, and the
projected spend has been printed and passed a cap.

TASK 5 rides alongside because the same incident proved the point: the 8
credits per person bought MOBILE numbers, and a switchboard, a direct line
and a personal handset are completely different objects in a sales motion.
Every number therefore carries a type and a receipt of how it got one.

OPERATOR RULING 2026-08-06: nothing is withheld from the client artifact.
Mobiles render. What survives is the integrity rule underneath, which is a
different thing from a withholding policy: a number whose type the response
never established does not render, because an unlabeled number is not a
fact about anybody.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import phone_policy  # noqa: E402
from tools import apollo_credits as credits  # noqa: E402
from tools import apollo_targets  # noqa: E402


# ---- TASK 2(a) · a billable call cannot fire on an unproven path ---------- #
def test_a_billable_call_refuses_when_its_retrieval_path_never_answered():
    """THE REQUIRED TEST. No probe attempted at all: the reveal refuses."""
    proof = credits.RetrievalProof()
    with pytest.raises(credits.RetrievalUnproven) as exc:
        proof.require(apollo_targets.WEBHOOK_RESULT_PATH)
    assert "has not answered in this run" in str(exc.value)
    assert "16 direct-dial credits" in str(exc.value)


def test_a_failed_probe_is_a_failed_proof_not_a_crash():
    """The 2026-08-06 shape exactly: the probe raises an auth error, so the
    path is recorded unproven and the billable call still refuses."""
    proof = credits.RetrievalProof()

    def refuses():
        raise RuntimeError("This connector requires authentication")

    assert proof.prove(apollo_targets.WEBHOOK_RESULT_PATH, refuses) is False
    assert proof.is_proven(apollo_targets.WEBHOOK_RESULT_PATH) is False
    with pytest.raises(credits.RetrievalUnproven, match="authentication"):
        proof.require(apollo_targets.WEBHOOK_RESULT_PATH)


def test_a_successful_probe_unlocks_the_path_and_only_that_path():
    proof = credits.RetrievalProof()
    assert proof.prove("path.a", lambda: {"ok": True}) is True
    proof.require("path.a")                       # does not raise
    with pytest.raises(credits.RetrievalUnproven):
        proof.require("path.b")


def test_there_is_no_way_to_mark_a_path_proven_without_running_a_probe():
    """An 'assume it works' switch is the failure this class prevents, so
    the only writer of the proven map is prove()."""
    proof = credits.RetrievalProof()
    assert not hasattr(proof, "assume")
    assert not hasattr(proof, "force")


def test_the_phone_reveal_refuses_end_to_end_on_an_unproven_path(monkeypatch):
    """The guard where it actually matters: enrich() with a reveal, switch
    lifted, but no proven retrieval path. Nothing is called."""
    monkeypatch.setenv(apollo_targets.PHONE_REVEAL_ENV, "on")
    called = []
    with pytest.raises(credits.RetrievalUnproven):
        apollo_targets.enrich(
            [{"contact_id": "a" * 24}], key="k",
            matcher=lambda p: called.append(p) or {"matches": []},
            reveal_phone_number=True, proof=credits.RetrievalProof())
    assert called == [], "a billable call fired on an unproven path"


# ---- TASK 5 · the reveal is hard-gated off, independently ----------------- #
def test_the_phone_reveal_is_hard_gated_off_in_code(monkeypatch):
    """Two independent guards. This one answers 'do we want to spend', the
    proof answers 'could we ever collect what we paid for'."""
    monkeypatch.delenv(apollo_targets.PHONE_REVEAL_ENV, raising=False)
    assert apollo_targets.phone_reveal_allowed() is False
    called = []
    with pytest.raises(credits.CreditRefusal, match="hard-gated off"):
        apollo_targets.enrich(
            [{"contact_id": "a" * 24}], key="k",
            matcher=lambda p: called.append(p) or {"matches": []},
            reveal_phone_number=True)
    assert called == []


def test_email_only_enrichment_needs_neither_gate(monkeypatch):
    """The confirmed path stays open: email is synchronous, so it has no
    async retrieval path to prove and no dial gate to lift."""
    monkeypatch.delenv(apollo_targets.PHONE_REVEAL_ENV, raising=False)
    out = apollo_targets.enrich(
        [{"contact_id": "a" * 24, "name": "Dana Reyes"}], key="k",
        matcher=lambda p: {"matches": [
            {"id": "a" * 24, "email": "dana@irs.gov",
             "email_status": "verified"}]})
    assert out["rows"][0]["email"] == "dana@irs.gov"
    assert out["rows"][0]["email_status"] == "verified"
    assert out["calls"] == 1


# ---- TASK 2(b) · the pre-flight estimate and the cap --------------------- #
def test_the_estimate_prices_the_run_from_measured_rates():
    est = credits.estimate(24, calls=3, credit_class="match",
                           balance_before=3889)
    assert est.projected_total == 24
    assert est.projected_remaining == 3865
    assert "measured 2026-08-06" in est.basis
    printed = credits.render_estimate(est)
    for probe in ("people to enrich", "projected total", "balance before",
                  "projected remaining", credits.CREDIT_CAP_ENV):
        assert probe in printed


def test_a_run_over_the_cap_refuses_before_anything_bills(monkeypatch):
    monkeypatch.setenv(credits.CREDIT_CAP_ENV, "10")
    est = credits.estimate(24, calls=3, credit_class="match",
                           balance_before=3889)
    with pytest.raises(credits.CreditRefusal, match="exceeds the 10-credit"):
        credits.enforce_cap(est)


def test_a_run_exceeding_the_balance_refuses(monkeypatch):
    monkeypatch.setenv(credits.CREDIT_CAP_ENV, "100000")
    est = credits.estimate(500, calls=50, credit_class="match",
                           balance_before=10)
    with pytest.raises(credits.CreditRefusal, match="exceeds the balance"):
        credits.enforce_cap(est)


def test_the_default_cap_admits_a_full_single_client_pass(monkeypatch):
    """OPERATOR RULING 2026-08-06: coverage is never trimmed to fit a
    budget. Measured at book scale, one client's full pass at five people
    per account runs 45 to 360 credits. A cap below that turns into an
    invisible coverage limit, which is exactly what a 25-credit ceiling was
    doing. 500 admits any single-client full pass and still stops a
    runaway."""
    monkeypatch.delenv(credits.CREDIT_CAP_ENV, raising=False)
    assert credits.credit_cap() == credits.DEFAULT_CREDIT_CAP
    assert credits.DEFAULT_CREDIT_CAP >= 360     # red_hat, 8 accounts, 5 deep
    # a whole-book run (900) still needs a deliberate raise
    assert credits.DEFAULT_CREDIT_CAP < 900


def test_a_large_plan_alerts_and_still_runs(monkeypatch):
    """The operator asked to be ALERTED when a plan is large, and was
    explicit that excessive is only a matter of credits. So the alert states
    the number and the share of balance; it never shortens the pass."""
    monkeypatch.delenv(credits.CREDIT_CAP_ENV, raising=False)
    monkeypatch.setenv(credits.CREDIT_CAP_ENV, "2000")
    # book scale: 20 accounts x 5 people, the whole seven-client book
    est = credits.estimate(100, calls=20, credit_class="direct_dial",
                           balance_before=3805)
    assert est.projected_total == 800
    warning = est.alert()
    assert warning and "800 of 3805" in warning
    assert "21 percent" in warning
    assert "Coverage is not being reduced" in warning
    assert "*** CREDIT ALERT ***" in credits.render_estimate(est)
    credits.enforce_cap(est)                     # alerts, does not refuse


def test_a_routine_single_client_pass_does_not_cry_wolf(monkeypatch):
    """red_hat full coverage, 8 accounts deep 5, is 360 credits against a
    3805 balance. That is a normal pass and must not alert, or the alert
    stops meaning anything."""
    monkeypatch.delenv(credits.CREDIT_CAP_ENV, raising=False)
    est = credits.estimate(40, calls=8, credit_class="direct_dial",
                           balance_before=3805)
    assert est.projected_total == 320
    assert est.alert() is None
    assert "CREDIT ALERT" not in credits.render_estimate(est)


def test_a_malformed_cap_refuses_rather_than_guessing(monkeypatch):
    monkeypatch.setenv(credits.CREDIT_CAP_ENV, "lots")
    with pytest.raises(credits.CreditRefusal, match="not an integer"):
        credits.credit_cap()


def test_an_unknown_credit_class_refuses_rather_than_pricing_at_zero():
    with pytest.raises(credits.CreditRefusal, match="unknown credit class"):
        credits.estimate(5, calls=1, credit_class="telepathy")


def test_the_dial_rate_is_eight_times_the_match_rate():
    """The ratio that decided the design, pinned so a later edit that makes
    a reveal look cheap has to change this number on purpose."""
    assert credits.CREDIT_CLASSES["match"]["per_record"] == 1
    assert credits.CREDIT_CLASSES["direct_dial"]["per_record"] == 8


# ---- TASK 2(c) · the measured delta lands in the receipt ----------------- #
def test_the_spend_record_reports_the_measured_delta_and_its_accuracy():
    est = credits.estimate(2, calls=1, credit_class="match",
                           balance_before=3907)
    record = credits.SpendRecord(balance_before=3907, balance_after=3889,
                                 calls_made=1, estimate=est)
    receipt = record.receipt()
    assert receipt["credits_spent"] == 18
    assert receipt["credits_projected"] == 2
    # The real burn: 18 spent against 2 projected, because a phone reveal
    # rode along. The receipt says so rather than reporting the projection.
    assert receipt["estimate_accurate"] is False
    assert "18 credit(s) actually consumed" in record.render()


def test_an_unknown_balance_is_recorded_as_unknown_never_as_free():
    record = credits.SpendRecord(balance_before=None, balance_after=None,
                                 calls_made=1)
    receipt = record.receipt()
    assert receipt["credits_spent"] is None
    assert receipt["estimate_accurate"] is None


def test_a_balance_endpoint_that_misbehaves_returns_none_not_a_number():
    assert apollo_targets.read_balance(
        "k", getter=lambda: {"nothing": "useful"}) is None
    assert apollo_targets.read_balance(
        "k", getter=lambda: (_ for _ in ()).throw(RuntimeError("500"))) is None


# ---- TASK 5 · classification, with a receipt for every number ------------ #
def test_an_apollo_type_flag_is_the_strongest_authority():
    entry = phone_policy.classify("+1 202 555 0100", apollo_type="mobile")
    assert entry["type"] == phone_policy.MOBILE
    assert entry["basis_kind"] == "apollo_type_flag"


def test_an_unknown_apollo_type_is_unclassified_never_guessed():
    entry = phone_policy.classify("+1 202 555 0100", apollo_type="carrier_pigeon")
    assert entry["type"] == phone_policy.UNCLASSIFIED
    assert "refusing to guess" in entry["basis"]


def test_an_organisation_field_number_is_always_a_main_line():
    entry = phone_policy.classify(
        "+1 202-324-3000", source_field="organization.primary_phone")
    assert entry["type"] == phone_policy.ORG_MAIN
    assert entry["basis_kind"] == "apollo_field"


def test_a_person_number_equal_to_the_switchboard_is_caught_as_org_main():
    """The trap this exists for: the switchboard sitting in a person field
    would otherwise render as that person's direct line."""
    entry = phone_policy.classify(
        "+1 202-324-3000", source_field="phone_number",
        org_numbers=["+12023243000"])
    assert entry["type"] == phone_policy.ORG_MAIN
    assert entry["basis_kind"] == "org_number_match"


def test_a_number_with_no_established_type_is_unclassified():
    entry = phone_policy.classify("+1 202 555 0100", source_field="phone_number")
    assert entry["type"] == phone_policy.UNCLASSIFIED


def test_there_is_no_area_code_heuristic():
    """Guessing 'looks like a mobile' would put a wrong label on a real
    person's handset, so no digits-based inference exists."""
    for number in ("+1 917 555 0100", "+1 202 555 0100", "555-0100"):
        assert phone_policy.classify(number, source_field="phone_number")[
            "type"] == phone_policy.UNCLASSIFIED


# ---- TASK 5 · the render policy, fail closed ----------------------------- #
def test_main_line_and_direct_line_render_by_default(monkeypatch):
    monkeypatch.delenv(phone_policy.INCLUDE_MOBILE_ENV, raising=False)
    assert phone_policy.renders(
        {"number": "1", "type": phone_policy.ORG_MAIN}) is True
    assert phone_policy.renders(
        {"number": "1", "type": phone_policy.WORK_DIRECT}) is True


def test_mobile_and_unclassified_render_by_default_and_can_be_suppressed(
        monkeypatch):
    """OPERATOR RULING 2026-08-06: nothing is withheld from the client
    artifact. The switch survives so an engagement can suppress mobiles
    without a code change, but OFF is now the explicit act."""
    monkeypatch.delenv(phone_policy.INCLUDE_MOBILE_ENV, raising=False)
    mobile = {"number": "1", "type": phone_policy.MOBILE}
    unknown = {"number": "1", "type": phone_policy.UNCLASSIFIED}
    assert phone_policy.renders(mobile) is True
    assert phone_policy.renders(unknown) is True
    monkeypatch.setenv(phone_policy.INCLUDE_MOBILE_ENV, "off")
    assert phone_policy.renders(mobile) is False
    assert phone_policy.renders(unknown) is False


def test_anything_not_positively_classified_never_renders(monkeypatch):
    monkeypatch.setenv(phone_policy.INCLUDE_MOBILE_ENV, "on")
    for entry in (None, {}, {"number": ""}, {"number": "1"},
                  {"number": "1", "type": "invented"}):
        assert phone_policy.renders(entry) is False


def test_a_main_line_can_never_be_selected_as_the_direct_line():
    phones = [{"number": "+1 202-324-3000", "type": phone_policy.ORG_MAIN,
               "basis": "organization.primary_phone"}]
    assert phone_policy.select(phones, phone_policy.WORK_DIRECT) is None
    assert phone_policy.select(phones, phone_policy.ORG_MAIN)["number"] == \
        "+1 202-324-3000"


def test_the_switchboard_rides_the_enrichment_response_for_free():
    """Operator ruling: take the main line, in its own lane. It arrives on
    a response already paid for."""
    person = {"organization": {"phone": "+1 202-324-3000",
                               "primary_phone": {"number": "+1 202-324-3000"}}}
    entries = phone_policy.from_enrichment(person)
    assert len(entries) == 1
    assert entries[0]["type"] == phone_policy.ORG_MAIN


def test_a_mobile_from_a_reveal_is_classified_and_kept_out_of_the_dial(
        monkeypatch):
    """A mobile renders (operator ruling), but it is still NOT the dial: a
    reveal-sourced mobile may never be selected as work_direct."""
    monkeypatch.delenv(phone_policy.INCLUDE_MOBILE_ENV, raising=False)
    person = {"phone_numbers": [{"raw_number": "+1 917 555 0100",
                                 "type": "mobile"}],
              "organization": {"phone": "+1 202-324-3000"}}
    entries = phone_policy.from_enrichment(person)
    kinds = {e["type"] for e in entries}
    assert kinds == {phone_policy.MOBILE, phone_policy.ORG_MAIN}
    assert phone_policy.select(entries, phone_policy.WORK_DIRECT) is None
    assert phone_policy.select(entries, phone_policy.MOBILE)["number"] == \
        "+1 917 555 0100"
    assert phone_policy.withheld(entries) == []
