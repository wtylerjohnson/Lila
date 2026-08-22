"""Two internal rules, one collision, in the evidence dock.

Distinct records legitimately share a program name, and rendering them
identically reads as repeated prose. So a colliding title carries its own
record id INSIDE the title text (render.py, the `titles` map).

The dock ALSO prints that id in its own `sb-evidence-record` span, and
`check_dock` requires each record id to appear EXACTLY ONCE in visible dock
text. Keeping the suffix therefore renders the id twice and fails the press.

Found 2026-07-30 by the all-federal press: many Riverbed resale lines are
titled just "RIVERBED", so disambiguation fired on far more records than the
civilian pack ever produced, and the report shipped UNCERTIFIED with

    dock_record_count: 15F06724F0001868 appears 2x in visible dock text
    dock_record_count: HC102826F0379 appears 2x in visible dock text

The dock is the one band that does not need the suffix: its rows are already
distinguished by the id column itself.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import render as render_mod  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402
from agents.golden_press.validate import validate_press  # noqa: E402


def _record(rid: str, **kw) -> GoldenRecord:
    base = dict(record_id=rid, lane="L2_entity_award",
                title="RIVERBED", description="RIVERBED",
                agency="Department of Justice",
                sub_agency="Federal Bureau of Investigation",
                recipient="SWISH DATA CORPORATION",
                obligated_dollars=1_920_000.0, period_end="2027-07-31",
                url=f"https://www.usaspending.gov/award/CONT_AWD_{rid}")
    base.update(kw)
    return GoldenRecord(**base)


def _pack(records) -> EvidencePack:
    return EvidencePack(client_name="Riverbed",
                        generated_at="2026-07-30T12:00:00",
                        records=records, research={"entities": {}})


COLLIDING = [
    _record("15F06724F0001868"),
    _record("HC102826F0379", agency="Department of Defense",
            sub_agency="Defense Information Systems Agency",
            recipient="CARAHSOFT TECHNOLOGY CORP"),
]


def test_records_sharing_a_title_validate_clean():
    """CONTRACT (2026-08-03): the evidence dock is retired; inline linkage
    plus Show the work carry its function. Colliding titles must still
    validate clean end to end."""
    pack = _pack(COLLIDING)
    content = render_mod.render_content_region(pack, prose={})
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_colliding_titles_stay_distinguishable_where_they_render():
    """Two records titled RIVERBED must never read identically: the id
    rides in the rendered title text (shape()'s titles map), which is also
    what keeps colliding fragments out of repeated_prose."""
    content = render_mod.render_content_region(_pack(COLLIDING), prose={})
    for rid in ("15F06724F0001868", "HC102826F0379"):
        assert rid in content
    from agents.golden_press.validate import check_repeated_prose
    assert check_repeated_prose(content) == []


def test_every_colliding_id_renders_linked_canonically():
    pack = _pack(COLLIDING)
    content = render_mod.render_content_region(pack, prose={})
    from agents.golden_press.validate import check_links
    assert check_links(content, pack) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
