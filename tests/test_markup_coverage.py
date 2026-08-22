"""ONE MARKUP SYSTEM (repress tasking, 2026-08-03).

The delivered FiscalNote artifact rendered as unstyled text: the renderer
emitted legacy sb-* markup while the client stylesheet styled the template
library's classes. This test presses a representative pack through the REAL
press seam and fails, listing the complete sorted set, if the artifact emits
any class the embedded stylesheets do not define. No whitelist.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402


def _pack():
    def award(rid, sub, hits, recipient, end, dollars=1_000_000.0):
        return GoldenRecord(
            record_id=rid, lane="L2_entity_award",
            title=f"Enterprise agreement {rid.lower()}",
            description=f"Scope on the record for {rid}.",
            agency="Department of the Treasury", sub_agency=sub,
            recipient=recipient, entity_hits=list(hits),
            obligated_dollars=dollars, period_end=end,
            url=f"https://www.usaspending.gov/award/CONT_AWD_{rid}")

    records = [
        award("CONT2026C001", "Internal Revenue Service", ["AcmeFlow"], "FCN, INC.",
              "2026-12-31"),
        award("CONT2026R1X9", "Internal Revenue Service", ["RivalSuite"],
              "Rival Prime Inc", "2026-11-30", 400_000.0),
        GoldenRecord(record_id="F2026001", lane="L4_forecast",
                     title="Monitoring support forecast",
                     agency="Department of the Treasury",
                     sub_agency="Internal Revenue Service",
                     estimated_value_range="$1M to $5M",
                     url="https://apfs-cloud.dhs.gov/forecast/1"),
        GoldenRecord(record_id="N260801X1", lane="L1_notice",
                     title="Sources sought for monitoring",
                     agency="Department of the Treasury",
                     sub_agency="Internal Revenue Service",
                     response_deadline="2026-09-01",
                     url="https://sam.gov/opp/n260801x1/view"),
    ]
    pack = EvidencePack(
        client_name="Acme Networks", generated_at="2026-08-01T12:00:00Z",
        records=records,
        research={"entities": {"product": ["AcmeFlow"],
                               "competitor": ["RivalSuite"]},
                  "capability_terms": ["network monitoring"]})
    from agents.golden_press.decision_rules import build_decisions
    pack.decisions = build_decisions(pack)
    pack.events_screen = {"tier": "A", "source": "sam_extract",
                          "metered_quota_spent": 0, "scanned": 1000,
                          "event_type": 10, "event_language": 5,
                          "client_relevant": 0, "kept": 0}
    return pack


def _css_defined_names(css_text: str) -> set:
    """A real (if small) CSS parse: strip comments and strings, then walk
    tokens at selector level only (brace depth 0), collecting class and id
    selector names. Declaration blocks are never scanned, so a property
    value can neither hide nor invent a selector."""
    text = re.sub(r"/\*.*?\*/", " ", css_text, flags=re.S)
    out: set = set()
    depth = 0
    i, n = 0, len(text)
    selector_buf: list[str] = []
    while i < n:
        ch = text[i]
        if ch in "\"'":
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == "{":
            for m in re.finditer(r"[.#]([A-Za-z_][A-Za-z0-9_-]*)",
                                 "".join(selector_buf)):
                out.add(m.group(1))
            selector_buf = []
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            selector_buf.append(ch)
        i += 1
    return out


def test_every_emitted_class_is_defined_by_the_embedded_stylesheet(tmp_path):
    """Through the REAL press seam, both studio and client must be covered."""
    from pathlib import Path

    from agents.golden_press.press import _render_saved_inputs
    from agents.golden_press.validate import client_export

    root = Path(__file__).resolve().parents[1]
    content, spliced, verdict, _receipt = _render_saved_inputs(
        "Acme Networks", slug="acme-networks", root=root, pack=_pack(),
        prose={})
    client = client_export(content)

    assert client.count('<div id="board">') == 1

    for label, artifact in (("studio", spliced), ("client", client)):
        emitted: set = set()
        for m in re.finditer(r'class="([^"]+)"', re.sub(
                r"<style\b[^>]*>.*?</style>", " ", artifact,
                flags=re.S | re.I)):
            emitted.update(m.group(1).split())
        styled: set = set()
        for m in re.finditer(r"<style\b[^>]*>(.*?)</style>", artifact,
                             re.S | re.I):
            styled |= _css_defined_names(m.group(1))

        assert emitted, f"{label}: no classes emitted; coverage is vacuous"
        assert styled, f"{label}: no classes styled; coverage is vacuous"
        unstyled = sorted(emitted - styled)
        assert not unstyled, (
            f"{label}: emitted classes the stylesheet does not define: "
            + str(unstyled))


def test_planted_banned_vocabulary_in_the_hero_fails_certification():
    """HONEST VALIDATOR (repress tasking): 'research clocks' planted in the
    certified hero must FAIL; the plural defeated the old boundary and the
    old scope never scanned labels."""
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import validate_press

    pack = _pack()
    content = render_content_region(pack, prose={})
    assert validate_press(content, content, pack)["ok"], "fixture must be clean first"
    planted = content.replace(
        '<header class="hero"',
        '<header class="hero" aria-label="Award-period research clocks"', 1)
    verdict = validate_press(planted, planted, pack)
    assert not verdict["ok"]
    assert any(v["rule"] == "banned_vocabulary"
               for v in verdict["violations"])


def test_hero_show_work_reconciles_and_uses_local_press_date():
    """G1/G3: one library component proves the hero count, and the saved
    UTC invocation renders on the operator's Denver calendar date."""
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import validate_press

    pack = _pack().model_copy(update={
        "generated_at": "2026-08-04T00:28:47Z",
    })
    content = render_content_region(pack, prose={})
    assert "FEDERAL MARKET RESEARCH · 03 AUG 2026" in content
    assert '<details class="proof">' in content
    counts = [int(n) for n in re.findall(r'data-count="(\d+)"', content)]
    assert sum(counts) == len(pack.records)
    assert validate_press(content, content, pack)["ok"]


def test_hero_show_work_mismatch_fails_certification():
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import validate_press

    pack = _pack()
    content = render_content_region(pack, prose={})
    broken = content.replace('data-count="1"', 'data-count="99"', 1)
    verdict = validate_press(broken, broken, pack)
    assert any(v["rule"] == "hero_proof_mismatch"
               for v in verdict["violations"])


def test_sam_tripwire_raises_when_armed(monkeypatch):
    """Zero-SAM instrumentation: a config flag is not proof; an armed
    tripwire makes any metered SAM invocation raise."""
    import pytest as _pytest

    from tools.api.sam_gov import SamGovSource, SourceQuery
    monkeypatch.setenv("LILA_SAM_TRIPWIRE", "1")
    monkeypatch.setenv("SAM_GOV_API_KEY", "never-spent")
    source = SamGovSource()
    with _pytest.raises(RuntimeError, match="LILA_SAM_TRIPWIRE"):
        source.search(SourceQuery(query_terms=["x"],
                                  posted_from="2026-07-01",
                                  posted_to="2026-08-01"))


def test_planted_expired_respond_by_rendered_featured_fails():
    """TASK 3 (2026-08-04): a 2019 respond-by renders only in the collapsed
    closed-windows strip; planted as featured, certification FAILS."""
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import validate_press

    pack = _pack()
    stale = GoldenRecord(
        record_id="N201901OLD1", lane="L1_notice",
        title="Sources sought, long closed",
        agency="Department of the Treasury",
        sub_agency="Internal Revenue Service",
        response_deadline="2019-06-01",
        url="https://sam.gov/opp/n201901old1/view")
    pack.records.append(stale)
    content = render_content_region(pack, prose={})
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]     # collapsed strip is legal
    assert "CLOSED SHAPEABLE WINDOWS" in content
    # plant it featured: move the id outside the collapsed strip
    import re as _re
    strip = _re.search(r"<details.*?</details>",
                       _re.search(r'<section class="band" id="forward".*?</section>',
                                  content, _re.S).group(0), _re.S).group(0)
    planted = content.replace(strip, "").replace(
        '<section class="band" id="forward"',
        '<a href="https://sam.gov/opp/n201901old1/view">N201901OLD1</a>'
        '<section class="band" id="forward"', 1)
    # the plant must sit INSIDE the forward band to count as featured
    planted = content.replace(
        strip,
        '<p class="claim"><a href="https://sam.gov/opp/n201901old1/view">'
        'N201901OLD1</a></p>')
    verdict = validate_press(planted, planted, pack)
    assert any(v["rule"] == "shapeable_window_expired_featured"
               for v in verdict["violations"])


def test_treemaps_render_marks_as_boxes_from_table_sums():
    """House graph (2026-08-04): Bands 04/05 carry proportional-area
    treemaps whose boxes are the marks; every printed value is a sum the
    band's rows also print; All Other carries no dollar figure."""
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import validate_press

    pack = _pack()
    content = render_content_region(pack, prose={})
    assert 'aria-label="Prime recipients by cited' in content
    assert 'aria-label="Past obligations by buying agency' in content
    assert "patternContentUnits" in content     # marks fill their boxes
    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_treemap_terminates_when_the_aggregated_tail_outweighs_every_box():
    """THINKLOGICAL PRESS KILL (2026-08-04): entries arrive rank-descending
    with an aggregated 'All Other' appended LAST. When that tail outweighs
    the ranked boxes, the split accumulator only crossed half at the final
    item, so `first` became the whole list, `rest` was empty, and the
    layout recursed on an unchanged list until RecursionError took the
    press down after the prose call had already been paid for.

    Layout must terminate for ANY input order, place every positive entry
    exactly once, and keep areas proportional to value."""
    import threading

    from agents.golden_press.render import _squarify, _treemap_svg

    # The exact shape that killed the press: a small ranked head, a
    # dominant aggregated tail last.
    entries = [("Alpha Prime", 40_000.0, None, True),
               ("Beta Systems", 25_000.0, None, True),
               ("Gamma Federal", 10_000.0, None, True),
               ("All Other", 425_000.0, None, False)]

    rects: list = []
    done = threading.Event()

    def _layout():
        _squarify(sorted(entries, key=lambda e: (-e[1], str(e[0]))),
                  0, 0, 980, 420, rects)
        done.set()

    worker = threading.Thread(target=_layout, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert done.is_set(), "treemap layout did not terminate"

    assert len(rects) == len(entries)
    placed = {r[0][0] for r in rects}
    assert placed == {e[0] for e in entries}

    total_value = sum(e[1] for e in entries)
    canvas = 980.0 * 420.0
    for (name, value, _mark, _show), _x, _y, w, h in rects:
        assert w > 0 and h > 0, f"{name} got a degenerate box"
        assert abs((w * h) / canvas - value / total_value) < 0.02, (
            f"{name} area is not proportional to its value")

    # Ascending input (the pathological order) is laid out identically:
    # the renderer sorts, so caller row order cannot change the picture.
    svg_desc = _treemap_svg(entries, svg_id="tm-x", label="check")
    svg_asc = _treemap_svg(list(reversed(entries)), svg_id="tm-x",
                           label="check")
    assert svg_desc == svg_asc
    assert svg_desc.count("<rect") >= len(entries)


def test_planted_negative_rect_fails_certification():
    """The geometry gate is real: a dropped box must fail the press, not
    ship as a hole in the picture."""
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import validate_press

    pack = _pack()
    content = render_content_region(pack, prose={})
    assert validate_press(content, content, pack)["ok"]

    planted = content.replace('<rect ', '<rect width="-12.0" height="8.0" ', 1)
    verdict = validate_press(planted, planted, pack)
    assert not verdict["ok"]
    assert any(v["rule"] == "svg_geometry_non_positive"
               for v in verdict["violations"])


def _entityless_pack():
    """A pack whose spine is empty: no rival entities, so R1/R2/R4 return
    nothing and NO account is in play (the Thinklogical shape)."""
    pack = _pack()
    pack.research = {"entities": {}, "capability_terms": ["network monitoring"]}
    from agents.golden_press.decision_rules import build_decisions
    pack.decisions = build_decisions(pack)
    return pack


def test_empty_spine_still_renders_clocks_and_events_amendment_v1_2():
    """AMENDMENT v1.2 (operator: optimize for actionable content). With no
    accounts in play, binding clocks and events to accounts removed every
    one of them: the Thinklogical press printed '0 dated clocks' while the
    pack held 26, seven still open. The bands now fall back to the cited
    record set instead of printing a zero."""
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import validate_press

    pack = _entityless_pack()
    assert not (pack.decisions.get("r1") or {}).get("cards"), (
        "fixture must have an empty spine for this test to mean anything")

    content = render_content_region(pack, prose={})

    # Band 03 renders the pack's own clocks, not a zero-state.
    assert 'data-clock-table="1"' in content, (
        "clocks table must render from the record set when no account binds")
    assert "CLOCKS · NONE" not in content
    assert "shown against the record set itself" in content

    # The dated award records reach the table.
    for record in pack.records:
        if record.lane == "L2_entity_award" and record.period_end:
            assert record.record_id in content

    verdict = validate_press(content, content, pack)
    assert verdict["ok"], verdict["violations"]


def test_empty_band_refuses_model_prose():
    """An empty band states its own emptiness. Model prose in an empty
    forward lane named forecast records, dates and contacts the band did
    not render (CBP Land Mobile Radio, a TSA BPA on the Thinklogical
    press): an unbacked claim through the prose door."""
    from agents.golden_press.render import render_content_region

    pack = _entityless_pack()
    planted = ("CBP expects to solicit its Land Mobile Radio work on "
               "08/25/2026 and each record names a contact by email.")
    content = render_content_region(pack, prose={"band_forward": planted})
    assert "Land Mobile Radio" not in content
    assert "never summed" in content     # the deterministic lede stands

    # A band WITH rows still takes model prose.
    full = render_content_region(_pack(), prose={"band_forward": planted})
    assert "Land Mobile Radio" in full


def test_untested_competitive_zero_fails_certification():
    """GOLD-STANDARD BRIEF (2026-08-04). The Thinklogical press certified
    while printing 'HEAD-TO-HEAD · NONE' over a pack with zero competitor
    entities and zero competitor queries: it claimed a test that never ran.
    A tested zero is only allowed when rivals were supplied AND the lane
    actually searched them."""
    from agents.golden_press.render import (competitor_coverage,
                                            render_content_region)
    from agents.golden_press.validate import validate_press

    pack = _entityless_pack()
    coverage = competitor_coverage(pack)
    assert coverage["state"] == "not_supplied"
    assert coverage["tested_zero_allowed"] is False

    content = render_content_region(pack, prose={})
    # The honest copy is the forward-looking status, never a tested zero.
    assert "COMPETITIVE VALIDATION NEXT" in content
    assert "HEAD-TO-HEAD · NONE" not in content
    assert validate_press(content, content, pack)["ok"]

    planted = content.replace("COMPETITIVE VALIDATION NEXT",
                              "HEAD-TO-HEAD · NONE", 1)
    verdict = validate_press(planted, planted, pack)
    assert not verdict["ok"]
    assert any(v["rule"] == "competitor_zero_untested"
               for v in verdict["violations"])


def test_supplied_rivals_that_were_screened_may_state_a_tested_zero():
    """The other direction: rivals supplied and the lane ran, so a bounded
    'none found' statement is legitimate and carries its receipt."""
    from agents.golden_press.render import competitor_coverage

    pack = _pack()
    pack.research = dict(pack.research or {})
    pack.research["entities"] = {"competitor": ["RivalSuite"]}
    pack.queries = [{
        "lane": "L2_entity_award", "method": "entity:competitor",
        "endpoint": "https://api.usaspending.gov/api/v2/search/spending_by_award/",
        "body": {"filters": {"keywords": ["RivalSuite"]}},
        "executed_at": "2026-08-01T00:00:00Z",
        "result_count": 120, "kept_after_screen": 4,
    }]
    coverage = competitor_coverage(pack)
    assert coverage["state"] == "screened"
    assert coverage["tested_zero_allowed"] is True
    assert coverage["screened"] == ["RivalSuite"]
    assert coverage["returned"] == 120 and coverage["kept"] == 4


def test_client_copy_never_prints_internal_lane_ids():
    """Raw lane ids ('L2_competitor 0') are internal machinery; the brief
    puts them in the sidecars, not the deliverable."""
    from agents.golden_press.render import lane_label, render_content_region
    from agents.golden_press.validate import validate_press

    assert lane_label("L2_competitor") == "rival award records"
    assert lane_label("L1_notice") == "SAM notices"

    pack = _pack()
    content = render_content_region(pack, prose={})
    for raw in ("L2_competitor", "L2_entity_award", "L1_notice",
                "L4_forecast"):
        assert raw not in content, f"client copy leaks the lane id {raw!r}"

    planted = content.replace("</section>",
                              "<p>Screened L2_competitor 0</p></section>", 1)
    verdict = validate_press(planted, planted, pack)
    assert any(v["rule"] == "client_facing_internal_language"
               for v in verdict["violations"])
