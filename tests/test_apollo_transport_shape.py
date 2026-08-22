"""The Apollo transport, exercised against a RECORDED REAL RESPONSE.

tests/fixtures/apollo_people_search_response.json is not a hand-written
mock. It is the verbatim body Apollo returned on 2026-08-06 for the first
stored red_hat targeting spec, and this test drives tools/apollo_targets.py
with it so the next schema drift fails here instead of inside a client
artifact.

WHY THIS EXISTS. The supply step was written against an ASSUMED shape and
three of its assumptions were wrong. This test pins the real shape AND the
gaps, deliberately, in both directions:

  - if Apollo's response changes (a renamed key, a dropped field), the
    envelope and field assertions fail
  - if someone REPAIRS the store schema to hold what is currently dropped
    (email, phone, last_refreshed_at, total_entries), the gap assertions
    fail and force them to update this file on purpose

The gaps are recorded here, not fixed here. Repairing them is a schema
change and belongs to its own session with its own contract review.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import targets_store  # noqa: E402
from tools import apollo_targets  # noqa: E402

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "apollo_people_search_response.json")

# The spec this recording actually answered: red_hat's FBI displacement row,
# seeking persona tiers 1 and 2. Trimmed to the fields the transport reads.
_SPEC = {
    "spec_id": "t2-01-federal-bureau-of-investigation",
    "rule_id": "T2", "row_class": "displacement", "source_rule": "R1",
    "buying_component": "Federal Bureau of Investigation",
    "target_org": {"kind": "buying_component",
                   "name": "Federal Bureau of Investigation"},
    "seeks_tiers": [1, 2],
    "person_titles": ["Program Manager", "Chief Technology Officer",
                      "Contracting Officer", "Contract Specialist"],
    "person_seniorities": ["director", "manager"],
    "persona_titles": {
        "1": ["Program Manager", "Program Director",
              "Chief Information Officer", "Deputy Chief Information Officer",
              "Director of Network Operations", "Chief Technology Officer"],
        "2": ["Contracting Officer", "Contract Specialist",
              "Procurement Analyst", "Director of Acquisition",
              "Head of Contracting Activity", "Small Business Specialist"]},
    "join_record_ids": ["15F06725F0001715", "15F06725F0001351"],
    "join_records": [{"record_id": "15F06725F0001715",
                      "url": "https://www.usaspending.gov/award/X"}],
    "rationale": "why this call", "evidence": {}, "rule_basis": "basis",
}


def test_the_real_apollo_response_shape_drives_the_supply_step(monkeypatch):
    """One end-to-end pass over the recorded body: what the transport reads,
    what it produces, and every field the store silently drops."""
    monkeypatch.setenv("LILA_ENABLE_APOLLO", "on")
    recording = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body = recording["response"]

    # ---- 1. THE ENVELOPE, as recorded ------------------------------------ #
    assert set(body) == {"total_entries", "people"}, (
        "Apollo's people-search envelope changed; the step reads 'people' "
        "with a 'contacts' fallback and nothing else")
    people = body["people"]
    assert len(people) == 10 and body["total_entries"] == 104

    # ---- 2. THE PERSON SHAPE, as recorded -------------------------------- #
    keys = set().union(*(set(p) for p in people))
    assert keys == {"id", "first_name", "last_name", "title",
                    "last_refreshed_at", "linkedin_url", "organization"}, (
        f"the person shape drifted: {sorted(keys)}")
    # THE FALLBACK IS LOAD-BEARING, not defensive. There is no 'name' key at
    # all; _identity only produces a name because it joins first + last.
    assert all("name" not in p for p in people)
    assert all(p.get("first_name") and p.get("last_name") for p in people)
    # No masking on this plan tier: last names came back whole.
    assert not any("*" in str(p["last_name"]) for p in people)
    org_keys = set().union(*(set(p["organization"]) for p in people))
    assert org_keys == {"id", "name", "domain"}

    # ---- 3. THE GAPS, recorded rather than repaired ---------------------- #
    # The search endpoint returns NO contact details. Enrichment is a
    # separate, credit-charged call. These three assertions are why the
    # store renders no email and no phone today.
    assert all("email" not in p for p in people), (
        "an 'email' key appeared: the locked-email sentinel and the store's "
        "email slot are live again and this test must be updated")
    assert all("phone_number" not in p for p in people)
    assert all("phone" not in p["organization"] for p in people)

    # ---- 4. WHAT THE STEP PRODUCES FROM IT ------------------------------- #
    out = apollo_targets.resolve(
        [_SPEC], key="recorded-fixture", poster=lambda payload: body,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc))
    rows = out["contacts"]
    assert len(rows) == 10
    assert all(r["name"] and r["title"] and r["organization"] for r in rows)
    assert all(not r.get("email") for r in rows)      # nothing to store
    assert all(not r.get("phone") for r in rows)
    assert out["receipt"]["emails_locked"] == 0, (
        "the locked-email sentinel fired; on this endpoint no email key "
        "exists at all, so a nonzero count means the shape changed")

    # ---- 5. TIER ATTRIBUTION SURVIVES REAL, MESSY TITLES ----------------- #
    # The mock used clean strings. Real titles carry prefixes, suffixes and
    # punctuation, and substring matching is what makes them land.
    by_title = {r["title"]: r["tier"] for r in rows}
    assert by_title["Supervisory Contracting Officer"] == 2
    assert by_title["Unit Chief/ Contracting Officer"] == 2
    assert by_title["Senior Contract Specialist"] == 2
    assert by_title["Cybersecurity Program Manager, CIO's Office"] == 1
    assert by_title["Chief Technology Officer"] == 1

    # ---- 6. EVERY ROW STILL PASSES THE BAND'S ADMISSION LAWS ------------- #
    kept, dropped = targets_store.admissible(out, spec_ids=[_SPEC["spec_id"]])
    assert len(kept) == 10 and sum(dropped.values()) == 0
    assert all(r["provenance"]["class"] == "apollo" for r in kept)
    assert all(r["provenance"]["retrieved_at"] == "2026-08-06" for r in kept)

    # ---- 7. FIELDS THE RESPONSE CARRIES AND THE STORE DROPS -------------- #
    # Recorded so the loss is visible. Repairing any of these is a schema
    # change; when it happens, this assertion is the thing that fails.
    stored_keys = set().union(*(set(r) for r in rows))
    # REPAIRED 2026-08-06 (operator: put LinkedIn on the lead list). The
    # response always carried it; the store now keeps it as an identity
    # field under the provenance law, and the name links to it.
    assert any("linkedin_url" in p for p in people)
    assert "linkedin_url" in stored_keys
    # STILL A RECORDED GAP. Apollo states its OWN per-record freshness and
    # the store keeps only the date WE fetched, which flatters the data.
    # When this is repaired, this assertion is what forces the update.
    assert any("last_refreshed_at" in p for p in people)
    assert "last_refreshed_at" not in stored_keys, (
        "last_refreshed_at is now stored; the schema was repaired and this "
        "recorded-gap assertion must be updated deliberately")
    # Apollo states its OWN freshness per record; the store stamps only the
    # date WE fetched. On this recording they differ by up to seven weeks.
    assert min(p["last_refreshed_at"] for p in people)[:10] == "2026-06-17"
    assert out["receipt"]["retrieved_at"] == "2026-08-06"
    # The universe is 104; the receipt reports only the 10 that came back.
    assert out["receipt"]["result_count"] == 10
    assert "total_entries" not in out["receipt"]
