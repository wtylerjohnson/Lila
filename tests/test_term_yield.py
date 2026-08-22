"""The vocabulary receipt: term, count, up to three matched titles. Nothing
more.

OPERATOR SPEC 2026-07-31. A count-only receipt productizes the misleading
half of the measurement: "APM: 3" reads as signal until the titles show a
Navy sling assembly, a part number, and an Assistant Program Manager named
Tony Prudhomme. The titles sitting next to the count end the argument in
two seconds. And there is deliberately NO dead-vocabulary alarm: Riverbed's
all-zero vocabulary was TRUE (its market moves as reseller renewals that
never post publicly), and an alarm that fires on a true zero teaches
distrust of true zeros.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.query_terms import append_term_yield_log, term_yield  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from tools import notice_store as ns

    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "store"))
    conn = ns.connect(tmp_path / "store" / "notices.db")
    rows = [
        ("n1", "SLING ASSEMBLY, APM VERTICAL LIFT",
         "hardware for vertical lift"),
        ("n2", "Depot maintenance order",
         "part numbers 80012028/APM 1.8 and R1292"),
        ("n3", "Network services support",
         "contact the APM, Tony Prudhomme, for responses"),
        ("n4", "Enterprise monitoring platform",
         "network performance monitoring for the enterprise WAN"),
        ("n5", "APMX conference support", "no standalone token here"),
    ]
    for nid, title, desc in rows:
        conn.execute(
            "INSERT INTO notices (notice_id, title, description_prefix, "
            "first_seen, last_seen) VALUES (?,?,?,?,?)",
            (nid, title, desc, "2026-07-28", "2026-07-28"))
    conn.commit()
    return conn


def test_the_titles_ride_beside_the_count(store):
    out = term_yield(["APM"], conn=store)
    assert out == [{
        "term": "APM", "count": 3,
        "sample_titles": ["SLING ASSEMBLY, APM VERTICAL LIFT",
                          "Depot maintenance order",
                          "Network services support"],
    }]
    # the receipt IS the falsification test: the first title alone says fish


def test_a_true_zero_is_a_row_not_an_alarm(store):
    out = term_yield(["unified observability"], conn=store)
    assert out == [{"term": "unified observability", "count": 0,
                    "sample_titles": []}]
    # no verdict key, no status, no alarm - pinned by exact equality above


def test_word_boundaries_hold(store):
    """"APM" must not match inside "APMX", and phrases match whole."""
    out = {r["term"]: r["count"] for r in term_yield(
        ["APM", "network performance monitoring", "network"], conn=store)}
    assert out["APM"] == 3                    # n5's APMX excluded
    assert out["network performance monitoring"] == 1
    assert out["network"] == 2                # n3 title + n4 description


def test_the_sample_cap_is_three_and_the_count_is_not(store):
    for i in range(6):
        store.execute(
            "INSERT INTO notices (notice_id, title, description_prefix, "
            "first_seen, last_seen) VALUES (?,?,?,?,?)",
            (f"x{i}", f"Widget program {i}", "widget procurement",
             "2026-07-28", "2026-07-28"))
    store.commit()
    out = term_yield(["widget"], conn=store)
    assert out[0]["count"] == 6
    assert len(out[0]["sample_titles"]) == 3


def test_an_empty_store_yields_nothing_and_never_raises(tmp_path, monkeypatch):
    from tools import notice_store as ns

    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty"))
    conn = ns.connect(tmp_path / "empty" / "notices.db")
    assert term_yield(["anything"], conn=conn) == []


def test_empty_vocabulary_yields_nothing(store):
    assert term_yield([], conn=store) == []
    assert term_yield(["", "  "], conn=store) == []


# --------------------------------------------------------------------------- #
# the corpus log: one append, nothing built on top
# --------------------------------------------------------------------------- #
def test_the_log_appends_one_jsonl_line(tmp_path, monkeypatch):
    target = tmp_path / "yield.jsonl"
    monkeypatch.setenv("LILA_TERM_YIELD_LOG", str(target))
    entry = {"client": "Riverbed", "generated_at": "2026-07-31T00:00:00Z",
             "store_terms": [{"term": "APM", "count": 3,
                              "sample_titles": ["SLING ASSEMBLY"]}]}
    append_term_yield_log(entry)
    append_term_yield_log(entry)
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == entry


def test_an_unwritable_log_never_raises(monkeypatch):
    monkeypatch.setenv("LILA_TERM_YIELD_LOG", "/nonexistent-root/x/y.jsonl")
    append_term_yield_log({"client": "x"})    # silence is the assertion


# --------------------------------------------------------------------------- #
# the display glance never deletes the payload (operator catch, 2026-07-31)
# --------------------------------------------------------------------------- #
def test_the_glance_keeps_the_deciding_word():
    """The receipt's FIRST live output right-truncated 'NOTICE OF INTENT TO
    SOLE-SOURCE TO INNOVASEA' one word before INNOVASEA. SAM front-loads
    boilerplate and back-loads content; the glance must keep the tail."""
    from tools.query_terms import title_glance

    title = ("REQUIREMENTS-26-4191 NOTICE OF INTENT TO SOLE-SOURCE "
             "TO INNOVASEA")
    glance = title_glance(title, 40)
    assert "INNOVASEA" in glance
    assert len(glance) <= 41


def test_a_short_title_passes_untouched():
    from tools.query_terms import title_glance

    assert title_glance("Widget program", 96) == "Widget program"


def test_stored_sample_titles_are_always_verbatim(store):
    """The glance is display-only; the receipt itself stores full titles."""
    long_title = ("REQUIREMENTS-26-4191 NOTICE OF INTENT TO SOLE-SOURCE TO "
                  "INNOVASEA MARINE SYSTEMS CANADA FOR ACOUSTIC TELEMETRY "
                  "EQUIPMENT AND SUPPLIES PUGET SOUND")
    store.execute(
        "INSERT INTO notices (notice_id, title, description_prefix, "
        "first_seen, last_seen) VALUES (?,?,?,?,?)",
        ("long1", long_title, "acoustic telemetry equipment",
         "2026-07-28", "2026-07-28"))
    store.commit()
    out = term_yield(["telemetry"], conn=store)
    assert out[0]["sample_titles"][0] == long_title


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
