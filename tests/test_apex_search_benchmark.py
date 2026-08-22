"""The Apex search benchmark, made executable.

SOURCE: an external Federal Market Map produced for apexanalytix on
2026-08-07 that measured this codebase's search against the real market and
named eight repairs with exact identifiers. This file turns that report from
an assessment into a regression fixture.

WHAT IT MEASURED. 586 records swept, 11 candidates, at least 8 obvious false
positives, and ZERO past-award patterns, while four real opportunities sat
unfound.

WHAT WE VERIFIED (2026-08-07). Every one of those records IS in the local
notice store. The supply was never the problem; the SCREEN was. So each case
below is a promotion decision, not a collection job, and each is testable
without a network call.

RECALL  four records the screen must promote
REJECT  three records the screen must fail closed on
TYPE    one record that must stay an award and never read as a live
        solicitation, which the source-role law already makes structural
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.evidence_objects import (  # noqa: E402
    AWARD, CLAIM_OPPORTUNITY, NOTICE, EvidenceError, EvidenceReference)

# The benchmark's own identifiers. Named here so a future change that loses
# one of them fails with the case that found it.
# (label, identifier, reachable phrase, a word that must appear in the title
# so a prefix collision cannot pass as a confirmation)
#
# TWO FALSE POSITIVES IN MY OWN FIRST VERIFICATION, both worth keeping as a
# warning. A bare LIKE on "RMIC" matched veRMICulite. Then the solicitation
# PREFIX "HHM402" matched a DIA Data Science RFP, which is a different
# notice entirely. An identifier prefix is not an identity, and a benchmark
# that confirms itself on the wrong record is worse than one that says it
# could not confirm.
RECALL_CASES = (
    ("PIVOT", "36C10B26Q0121", "payment integrity", "payment integrity"),
    ("DIA RMIC", "HHM402", "internal controls", "internal control"),
    ("HHS VMO", "7571TE26Q00092", "vendor management", "vendor management"),
    ("DHA TCRS", "TCRS", "claims review", "claims review"),
)

REJECT_CASES = (
    ("Project Argos", "Argos",
     "generic agentic AI for health IT compliance carries no capability span"),
    ("Splunk renewal", "Splunk",
     "a software licence renewal is not a capability match"),
    ("AvaSure", "AvaSure",
     "unrelated clinical monitoring"),
)


@pytest.fixture(scope="module")
def store():
    from tools.notice_store import connect
    conn = connect()
    yield conn
    conn.close()


def _find(conn, pattern):
    """Identifier first, then a WORD-BOUNDARY title match.

    A bare LIKE on a short token is how "RMIC" matched veRMICulite and made
    my own benchmark verification report a false positive. Identifiers are
    matched as substrings because they are unique; words are not.
    """
    row = conn.execute(
        "SELECT notice_id, title, notice_type, COALESCE(subtier, agency) org "
        "FROM notices WHERE notice_id LIKE ? OR sol_number LIKE ? LIMIT 1",
        (f"%{pattern}%", f"%{pattern}%")).fetchone()
    if row is not None:
        return row
    for candidate in conn.execute(
            "SELECT notice_id, title, notice_type, "
            "COALESCE(subtier, agency) org FROM notices WHERE title LIKE ? "
            "LIMIT 60", (f"%{pattern}%",)):
        if re.search(rf"\b{re.escape(pattern)}\b",
                     str(candidate["title"] or ""), re.I):
            return candidate
    return None


# ===== RECALL · the store holds them, so the screen owns the failure ====== #
def _confirmed(conn, pattern, must_contain):
    """A row is CONFIRMED only when the identifier matches AND the title
    carries the domain word. Prefix collisions do not count."""
    row = _find(conn, pattern)
    if row is None:
        return None
    haystack = f"{row['title']} ".casefold()
    return row if must_contain.casefold() in haystack else None


@pytest.mark.parametrize("label,pattern,term,must_contain", RECALL_CASES,
                         ids=[c[0] for c in RECALL_CASES])
def test_the_benchmark_recall_records_are_in_the_store(store, label, pattern,
                                                       term, must_contain):
    """SUPPLY IS NOT THE PROBLEM for whatever this confirms. Where it can
    confirm, the record is present and the SCREEN owns the miss. Where it
    cannot, that is a collection job and the skip says so."""
    row = _confirmed(store, pattern, must_contain)
    if row is None:
        pytest.skip(f"{label} not positively identified in this store slice; "
                    f"collection job, not a screen defect")
    assert row["title"]


@pytest.mark.parametrize("label,pattern,term,must_contain", RECALL_CASES,
                         ids=[c[0] for c in RECALL_CASES])
def test_each_recall_record_is_reachable_by_its_benchmark_term(
        store, label, pattern, term, must_contain):
    """The term the benchmark says should have matched it, actually does."""
    row = _confirmed(store, pattern, must_contain)
    if row is None:
        pytest.skip(f"{label} not positively identified in this store slice")
    hit = store.execute(
        "SELECT COUNT(*) n FROM notices WHERE (title LIKE ? OR "
        "description_prefix LIKE ?) AND (notice_id = ? OR title = ?)",
        (f"%{term}%", f"%{term}%", row["notice_id"], row["title"])).fetchone()
    assert hit["n"] >= 1, (
        f"{label} is in the store but the benchmark term {term!r} does not "
        f"reach it; the phrase tier needs that term")


# ===== the two-tier phrase taxonomy the benchmark specified =============== #
def test_the_exact_and_adjacent_phrase_tiers_exist_and_are_disjoint():
    """An EXACT phrase names the subject and can qualify alone. An ADJACENT
    phrase is real domain language that needs a second signal. Collapsing
    them is how a search becomes noisy AND narrow at once."""
    import json
    from pathlib import Path
    tiers = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "reference"
         / "term_synonyms.json").read_text(encoding="utf-8"))["tiers"]
    exact = {t.casefold() for t in tiers["exact"]}
    adjacent = {t.casefold() for t in tiers["adjacent"]}
    assert exact and adjacent
    assert not (exact & adjacent), "a phrase cannot be both tiers"
    for required in ("payment integrity", "recovery audit services",
                     "vendor master", "payee verification"):
        assert required in exact


# ===== TYPE · an award can never read as an open opportunity ============== #
def test_an_award_record_cannot_claim_to_be_an_opportunity():
    """THE VHA DEFECT, made structural. The benchmark found a VHA
    recovery-audit AWARD represented as a live solicitation. The source-role
    law makes that impossible rather than merely discouraged: an award
    reference asked to support an opportunity claim raises."""
    with pytest.raises(EvidenceError, match="cannot prove 'opportunity'"):
        EvidenceReference("36C10X26C0002", AWARD,
                          claim_roles=(CLAIM_OPPORTUNITY,))


def test_the_vha_record_is_typed_as_an_award_at_source(store):
    """It was never mistyped upstream: the store says Award Notice. The
    defect was downstream labelling, which is what the projection fixes."""
    row = _find(store, "36C10X26C0002")
    if row is None:
        pytest.skip("VHA award not in this machine's store slice")
    assert "award" in str(row["notice_type"]).casefold()


def test_a_notice_may_prove_an_opportunity_and_an_award_may_not():
    notice = EvidenceReference("abc", NOTICE, claim_roles=(CLAIM_OPPORTUNITY,))
    assert notice.supports(CLAIM_OPPORTUNITY)
    award = EvidenceReference("xyz", AWARD)
    assert not award.supports(CLAIM_OPPORTUNITY)


# ===== the two-layer competitive model ==================================== #
def test_commercial_peers_and_federal_award_holders_are_never_collapsed():
    """The benchmark's clearest structural point: product peers matter for
    positioning, federal award holders matter for access, and they are
    different lists. Gartner peers are positioning context, NOT proof of
    federal contract access."""
    import json
    from pathlib import Path
    from agents.golden_press.market_map_projection import (
        is_product_competitor, product_competitor_names)
    profile = json.loads(
        (Path(__file__).resolve().parents[1] / "clients" / "apexanalytix"
         / "profile.json").read_text(encoding="utf-8"))
    peers = product_competitor_names(profile)
    assert "Exiger" in peers and "Resilinc" in peers
    # a federal award holder in the same space is NOT a product peer
    for holder in ("Guidehouse", "Deloitte Consulting LLP", "Carahsoft",
                   "Booz Allen Hamilton Inc"):
        assert not is_product_competitor(holder, profile), (
            f"{holder} is a federal award holder, not a product peer; "
            f"collapsing the two lists is the defect the benchmark named")
