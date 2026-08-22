"""A bare count in prose: checkable, or discarded with the sentence.

OPERATOR RULING 2026-07-30 (second pass). The first ruling let prose cite a
pack figure in its own voice. The residue rule behind it then required NO
digit to survive once verified dollars and dates were removed, and on the
2026-07-30 Riverbed press that discarded 17 of 18 opportunity justifications
whose every dollar and every date was verified. The sentence

    "The Internal Revenue Service carries $62,848,980 obligated across 4
     cited records, and it is actionable now because ..."

died on the 4. The pack holds exactly 4 Internal Revenue Service records, so
that count was evidence.

The rule now: a bare integer survives only when it counts something the
sentence NAMES. Everything else digit-shaped still disqualifies, because the
anti-hallucination guarantee is the whole point of the gate.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.compose import (  # noqa: E402
    _pack_count_candidates, _prose_usable,
)
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402


def _record(rid: str, agency: str, sub: str | None = None, **kw):
    base = dict(record_id=rid, lane="L2_entity_award", agency=agency,
                sub_agency=sub, recipient="Acme Networks",
                obligated_dollars=1_000_000.0, period_end="2027-03-31",
                title="Monitoring support", description="Monitoring support",
                url=f"https://www.usaspending.gov/award/{rid}")
    base.update(kw)
    return GoldenRecord(**base)


def _pack() -> EvidencePack:
    """Four IRS records, two VA, one USAID. The VA pair deliberately repeats
    its name in BOTH agency and sub_agency: counting that record twice would
    reject the honest number, which is exactly the live shape that broke."""
    records = [
        _record("IRS1", "Department of the Treasury", "Internal Revenue Service"),
        _record("IRS2", "Department of the Treasury", "Internal Revenue Service"),
        _record("IRS3", "Department of the Treasury", "Internal Revenue Service"),
        _record("IRS4", "Department of the Treasury", "Internal Revenue Service"),
        _record("VA1", "Department of Veterans Affairs",
                "Department of Veterans Affairs"),
        _record("VA2", "Department of Veterans Affairs",
                "Department of Veterans Affairs"),
        _record("AID1", "Agency for International Development"),
    ]
    return EvidencePack(client_name="Riverbed",
                        generated_at="2026-07-30T09:00:00", records=records,
                        research={"entities": {}})


# --------------------------------------------------------------------------- #
# the count the pack can prove
# --------------------------------------------------------------------------- #
def test_a_count_the_pack_proves_is_admitted():
    assert _prose_usable(
        "The Internal Revenue Service carries obligated across 4 cited "
        "records, and that is where the renewal pressure sits.", _pack())


def test_a_count_the_pack_contradicts_is_discarded():
    """The guarantee that survives this change: the model cannot state a
    count the evidence refutes."""
    assert _prose_usable(
        "The Internal Revenue Service carries obligated across 9 cited "
        "records, and that is where the renewal pressure sits.",
        _pack()) is None


def test_a_repeated_agency_name_counts_the_record_once():
    """VA appears as both agency and sub_agency on both records. Counting it
    twice would make the honest 2 read as 4 and reject the true sentence."""
    assert _prose_usable(
        "The Department of Veterans Affairs carries obligated across 2 cited "
        "records, which is the whole of its position here.", _pack())
    assert _prose_usable(
        "The Department of Veterans Affairs carries obligated across 4 cited "
        "records, which is the whole of its position here.", _pack()) is None


def test_the_pack_total_is_admitted():
    assert _prose_usable(
        "This assessment rests on 7 records drawn from the federal award "
        "history, each one cited in the dock below.", _pack())


def test_a_count_for_a_buyer_the_sentence_never_names_is_discarded():
    """A count is checkable only against something the sentence names. USAID
    holds 1 record, but this sentence never says USAID."""
    assert _prose_usable(
        "A single cited record anchors the position, and 1 more sits just "
        "outside the boundary where nobody named it.", _pack()) is None


# --------------------------------------------------------------------------- #
# everything else digit-shaped still disqualifies
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,why", [
    ("USCG/CG-C5I owns this sustainment call and will decide the follow-on "
     "well before the incumbent runs out.", "digit inside an identifier"),
    ("DHS values NOSC Network, Cloud and Cyber Services 2.0 and the stated "
     "purpose includes network and cloud platform work.", "a version number"),
    ("USCG values this COMPASS sustainment call at $50M to $100M and the "
     "solicitation covers design and OEM upgrades.", "abbreviated magnitude"),
    ("The requirement covers over 5,000 Cisco devices across more than 600 "
     "sites, which is the scale the incumbent already runs.", "scale figures"),
    ("Roughly 40% of this corridor renews inside the year, which is the "
     "window worth working right now.", "a percentage"),
])
def test_a_digit_that_is_not_a_provable_count_is_still_discarded(text, why):
    assert _prose_usable(text, _pack()) is None, why


def test_an_unbacked_dollar_is_still_discarded():
    """The dollar and date checks are untouched by this change."""
    assert _prose_usable(
        "The Internal Revenue Service obligated $8,675,309.00 on an award "
        "that anchors this whole account position.", _pack()) is None


def test_a_backed_dollar_still_survives():
    assert _prose_usable(
        "The Internal Revenue Service obligated $1,000,000.00 on an award "
        "that anchors this whole account position.", _pack())


def test_without_a_pack_the_strict_rule_still_stands():
    """No pack means nothing to check against, so no figure is admitted,
    counts included."""
    assert _prose_usable(
        "The Internal Revenue Service carries 4 cited records here, and that "
        "is where the renewal pressure sits.") is None


def test_figure_free_prose_is_untouched():
    assert _prose_usable(
        "The largest active award anchors this estate in a single bureau.",
        _pack())


# --------------------------------------------------------------------------- #
# the candidate set itself
# --------------------------------------------------------------------------- #
def test_candidates_bind_to_names_the_text_actually_uses():
    named = _pack_count_candidates(
        "The Internal Revenue Service is the anchor buyer here.", _pack())
    assert 4 in named                       # IRS records
    assert 7 in named                       # the pack total is always allowed
    assert 2 not in named                   # VA is never named in this text


def test_a_short_name_cannot_smuggle_a_count():
    """A two or three character name would match inside unrelated words ("VA"
    inside "evaluation"), so it is not allowed to vouch for a count. The pack
    here holds 2 VA records among 5 total, so 2 is only reachable through the
    short name and must not be admitted."""
    pack = EvidencePack(
        client_name="Riverbed", generated_at="2026-07-30T09:00:00",
        records=[_record("X1", "VA"), _record("X2", "VA"),
                 _record("Y1", "Department of Commerce"),
                 _record("Y2", "Department of Commerce"),
                 _record("Y3", "Department of Commerce")],
        research={"entities": {}})
    candidates = _pack_count_candidates("This is evaluation prose.", pack)
    assert 5 in candidates          # the pack total is always allowed
    assert 2 not in candidates      # "VA" inside "evaluation" vouches for nothing


# --------------------------------------------------------------------------- #
# the reload path judges prose the same way compose did
# --------------------------------------------------------------------------- #
def test_stored_prose_reloads_under_the_rule_that_accepted_it(tmp_path):
    """REGRESSION 2026-07-30: `_load_prose_sidecar` called `_prose_usable`
    WITHOUT the pack, so it applied the no-figure rule to prose the figure
    ruling deliberately admitted. Every re-render of a current client died on
    its own stored prose. Two call sites of one rule must not disagree."""
    import json

    from agents.golden_press import press as press_mod
    from agents.golden_press.render import prose_slots

    pack = _pack()
    slot = prose_slots(pack)[0]["key"]
    text = ("The Internal Revenue Service carries obligated across 4 cited "
            "records, and that is where the renewal pressure sits.")
    path = tmp_path / "prose.json"
    path.write_text(json.dumps({
        "schema_version": press_mod.PROSE_SIDECAR_SCHEMA_VERSION,
        "client_name": pack.client_name,
        "evidence_pack_sha256": press_mod._payload_sha256(
            press_mod._pack_payload(pack)),
        "prose": {slot: text},
    }), encoding="utf-8")
    assert press_mod._load_prose_sidecar(path, pack) == {slot: text}


# --------------------------------------------------------------------------- #
# the names a writer actually uses
#
# REGRESSION 2026-07-30: the first count fix required the pack's exact string
# ("Internal Revenue Service") to appear in the sentence. The model writes
# "the IRS" and "Social Security", so no count was ever vouched for and every
# corridor justification died a second time with the fix supposedly in.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sentence", [
    "The IRS is the heaviest buyer in this pack across 4 cited records, and "
    "that is where the renewal pressure sits.",
    "Internal Revenue is the heaviest buyer across 4 cited records, and that "
    "is where the renewal pressure sits.",
    "The Internal Revenue Service leads across 4 cited records, and that is "
    "where the renewal pressure sits.",
])
def test_a_count_survives_the_short_forms_writers_use(sentence):
    assert _prose_usable(sentence, _pack())


def test_a_truncated_agency_name_still_vouches_for_its_count():
    """"Social Security" for the Social Security Administration."""
    pack = EvidencePack(
        client_name="Riverbed", generated_at="2026-07-30T09:00:00",
        records=[_record("S1", "Social Security Administration"),
                 _record("S2", "Social Security Administration"),
                 _record("Z1", "Department of Commerce")],
        research={"entities": {}})
    assert _prose_usable(
        "Social Security has obligated across 2 cited records, and both of "
        "them are live right now.", pack)


def test_an_acronym_matches_as_a_whole_word_only():
    """"IRS" must not be found inside "IRSA" or "FIRST"."""
    assert _prose_usable(
        "FIRST responders account for 4 cited records in this pack, which is "
        "not a claim the evidence supports.", _pack()) is None


# --------------------------------------------------------------------------- #
# years, quoted figures, and the hole between them
# --------------------------------------------------------------------------- #
def test_a_year_the_pack_carries_is_not_a_count():
    """REGRESSION: the first fix's regex captured "2029," (with the comma)
    and demanded it be a record count."""
    assert _prose_usable(
        "The option room on this award runs to 2027, which is the window "
        "worth working before it closes.", _pack())


def test_a_year_the_pack_does_not_carry_is_still_refused():
    assert _prose_usable(
        "The option room on this award runs to 2044, which is the window "
        "worth working before it closes.", _pack()) is None


def test_a_figure_quoted_from_a_record_survives():
    pack = _pack()
    pack.records[0].description = ("Requirement covers over 5,000 Cisco "
                                   "devices across 600 sites.")
    assert _prose_usable(
        "The requirement covers over 5,000 Cisco devices, which is the scale "
        "the incumbent already runs today.", pack)


def test_a_small_integer_cannot_ride_in_on_stray_record_text():
    """THE HOLE: a bare "4" occurs somewhere in 46 records' text by accident.
    Letting that license "across 4 cited records" would hand back the whole
    guarantee, so the quoted-figure route requires three characters or more."""
    pack = _pack()
    pack.records[0].description = "Delivery order 4 of the base period."
    assert _prose_usable(
        "The Department of Veterans Affairs carries 4 cited records, which "
        "overstates what the evidence shows.", pack) is None


def test_a_program_name_carrying_a_digit_survives_when_the_pack_says_it():
    pack = _pack()
    pack.records[0].set_aside = "8(a) sole source"
    assert _prose_usable(
        "This was awarded as an 8(a) sole source, so the follow-on will need "
        "a partner holding that status.", pack)


def test_a_program_name_the_pack_never_says_is_refused():
    assert _prose_usable(
        "This was awarded as an 8(a) sole source, so the follow-on will need "
        "a partner holding that status.", _pack()) is None


def test_a_quoted_number_must_stand_alone_not_hide_inside_a_bigger_one():
    """"600" must not ride in on "$1,600,000"."""
    pack = _pack()
    pack.records[0].description = "Base value of $1,600,000 for the period."
    assert _prose_usable(
        "The requirement spans 600 separate sites, which is the scale the "
        "incumbent already runs today.", pack) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
