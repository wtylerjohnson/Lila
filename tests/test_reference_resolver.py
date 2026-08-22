"""Identifier resolution, and the two API rules that made 15 real awards
look like they did not exist.

The fixture is RECORDED from the live USAspending responses, not written by
hand. A hand-written mock proves the mock is shaped the way its author
imagined; this one proves the code reads what the API actually sends.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agents.golden_press.evidence_objects import (
    AWARD, CLAIM_OPPORTUNITY, NOTICE, EvidenceError,
)
from agents.golden_press.reference_resolver import (
    UNRESOLVED, resolve, resolve_all,
)
from tools.api.usaspending import (
    AWARD_FIELDS, AWARD_SORT, AWARD_TYPE_GROUPS, recipient_awards,
    resolve_award,
)

_FIXTURE = (Path(__file__).parent / "fixtures"
            / "usaspending_piid_resolution.json")


@pytest.fixture()
def recorded():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _make_poster(recorded, seen=None):
    def _post(url, *, json=None, **kw):
        body = json or {}
        filters = body.get("filters") or {}
        codes = list(filters.get("award_type_codes") or [])

        # RULE 1: one group only. The live API answers 422 to a mixed list.
        groups = {name for name, members in AWARD_TYPE_GROUPS.items()
                  if set(codes) & set(members)}
        assert len(groups) <= 1, (
            "award_type_codes mixed contract and IDV groups; the live API "
            "rejects this with 422")

        # RULE 2: the sort key must be among the requested fields.
        assert body.get("sort") in (body.get("fields") or []), (
            "sorting on a field that was not requested is a 422 the message "
            "does not explain")

        if seen is not None:
            seen.append(codes)
        piid = (filters.get("award_ids") or [""])[0]
        rows = [r for r in recorded.get(piid, [])
                if r.get("_award_group") in groups]
        return {"results": rows}
    return _post


def test_award_fields_carry_the_sort_key():
    assert AWARD_SORT in AWARD_FIELDS


def test_award_type_groups_do_not_overlap():
    contract = set(AWARD_TYPE_GROUPS["contract"])
    idv = set(AWARD_TYPE_GROUPS["idv"])
    assert not contract & idv


def test_resolve_award_asks_each_group_separately(recorded):
    seen: list = []
    rows = resolve_award("SP470125F0285", poster=_make_poster(recorded, seen))
    assert len(seen) == len(AWARD_TYPE_GROUPS), (
        "a PIID may be a contract or an IDV, so both groups must be asked")
    assert rows and rows[0]["Recipient Name"]


def test_resolve_award_rejects_false_clean_success_schema():
    def false_clean(*args, **kwargs):
        return {"message": "maintenance"}

    with pytest.raises(RuntimeError, match="PIID resolution was incomplete"):
        resolve_award("SP470125F0285", poster=false_clean)


def test_recipient_awards_rejects_false_clean_success_schema():
    def false_clean(*args, **kwargs):
        return {"results": [{"message": "maintenance"}]}

    with pytest.raises(
        RuntimeError,
        match="recipient-award search was incomplete",
    ):
        recipient_awards("Acme", poster=false_clean)


def test_every_recorded_piid_resolves_to_an_award(recorded):
    post = _make_poster(recorded)
    for piid in recorded:
        entry = resolve(piid, poster=post)
        assert entry["kind"] == AWARD, f"{piid} should resolve to an award"
        assert entry["amount"] >= 0
        assert entry["reference"].source_kind == AWARD


def test_the_cited_awards_total_what_was_measured(recorded):
    post = _make_poster(recorded)
    out = resolve_all(list(recorded), poster=post)
    assert out["receipt"]["by_kind"][AWARD] == 15
    assert round(out["receipt"]["award_dollars"], 2) == 85549824.90


def test_an_award_reference_cannot_prove_an_opportunity(recorded):
    entry = resolve("SP470125F0285", poster=_make_poster(recorded))
    with pytest.raises(EvidenceError):
        entry["reference"].require(CLAIM_OPPORTUNITY)


def test_a_sources_sought_number_is_not_an_award(recorded):
    """N0003026SSN7001 is a Navy sources-sought NOTICE.

    The award API correctly returns nothing for it, and the resolver must
    say "notice we have not ingested" rather than "does not exist".
    """
    entry = resolve("N0003026SSN7001", poster=_make_poster(recorded))
    assert entry["kind"] == UNRESOLVED
    assert "notice" in entry["why"]
    assert entry["next_action"]


def test_notice_shaped_identifiers_skip_the_award_lookup(recorded):
    calls: list = []

    def _post(url, **kw):
        calls.append(url)
        return {"results": []}

    resolve("FA701426_FMF_FIAR", poster=_post)
    assert not calls, (
        "an identifier whose shape says notice should not cost an award "
        "round trip")


def _store(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "n.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE notices (notice_id TEXT, title TEXT, notice_type TEXT, "
        "sol_number TEXT, poc_email TEXT, agency TEXT, subtier TEXT)")
    conn.execute(
        "INSERT INTO notices VALUES (?,?,?,?,?,?,?)",
        ("04633cac95d541f48bbb13025945904d",
         "RFI - Fraud Detection and Payment Integrity Tools",
         "Special Notice", "2032L226N00008", "jason.schofield@treasury.gov",
         "TREASURY, DEPARTMENT OF THE", "DEPARTMENTAL OFFICES"))
    return conn


def test_the_local_store_answers_before_the_network(tmp_path):
    calls: list = []

    def _post(url, **kw):
        calls.append(url)
        return {"results": []}

    conn = _store(tmp_path)
    entry = resolve("2032L226N00008", conn=conn, poster=_post)
    assert entry["kind"] == NOTICE
    assert not calls, "a record already held must not cost a network call"
    assert entry["reference"].supports(CLAIM_OPPORTUNITY)
    conn.close()
