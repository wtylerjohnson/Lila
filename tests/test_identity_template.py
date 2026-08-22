"""Identity-template improvement (2026-07-12): imported client brand SVGs are
exempt from chart-oriented R13 remediation and keep their native viewBox; the
cover drops the redundant wordmark when a full logo exists; the Command Center
uses aspect-ratio-aware logo slots. Isolated from search/gate/release."""

from __future__ import annotations

import os
import re
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.svg_safety import (  # noqa: E402
    autofix_svg_margins, svg_margin_violations,
)


# ── R13 exemption: brand assets keep native geometry; charts do not ─────────
_WIDE_BRAND = ('<svg class="client-brand-wide" data-brand-asset="1" '
               'width="345.75" height="47.84" viewBox="0 0 345.75 47.84">'
               '<text x="0" y="0">NETSCOUT</text></svg>')
_CHART = ('<svg viewBox="0 0 200 100"><text x="0" y="0" font-size="14">'
          'axis label</text><rect x="0" y="0" width="40" height="10"/></svg>')


def test_brand_asset_is_exempt_from_geometry_violations():
    # text at x=0,y=0 would normally breach the 8-unit margin; the brand
    # marker exempts it so R13 never reshapes an imported wordmark
    assert svg_margin_violations(_WIDE_BRAND) == []


def test_generated_chart_still_reports_violations():
    assert svg_margin_violations(_CHART)  # unmarked chart stays governed


def test_autofix_leaves_a_brand_viewbox_byte_for_byte():
    html = f"<body>{_WIDE_BRAND}</body>"
    fixed, fixes = autofix_svg_margins(html)
    assert 'viewBox="0 0 345.75 47.84"' in fixed  # unchanged
    assert not any("345.75" in f or "47.84" in f for f in fixes)


def test_autofix_still_remediates_a_generated_chart():
    html = f"<body>{_CHART}</body>"
    fixed, fixes = autofix_svg_margins(html)
    assert fixes  # the chart WAS remediated
    assert 'viewBox="0 0 200 100"' not in fixed  # its viewBox expanded


def test_brand_marker_checked_on_opening_tag_only():
    # a chart whose CONTENT mentions the marker string is NOT exempted
    sneaky = ('<svg viewBox="0 0 200 100"><text x="0" y="0" font-size="14">'
              'data-brand-asset in body</text></svg>')
    assert svg_margin_violations(sneaky)


# ── report cover: full logo drops the wordmark; monogram keeps it ────────────
def _render_for(client_name, monkeypatch, tmp_path, with_logo):
    import agents.reports.capture_brief as cb
    if with_logo:
        d = tmp_path / "client"
        d.mkdir()
        (d / "testco.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="345" height="48" '
            'viewBox="0 0 345 48"><rect width="345" height="48"/></svg>')
        monkeypatch.setattr(cb, "_resolve_client_mark_path",
                            lambda name: (d / "testco.svg"))
    else:
        monkeypatch.setattr(cb, "_resolve_client_mark_path", lambda name: None)
    from tests.test_capture_brief import _content
    return cb.render_capture_brief(_content(client_name=client_name))


def test_full_logo_drops_redundant_wordmark_and_keeps_native_viewbox(
        tmp_path, monkeypatch):
    from agents.reports.svg_safety import autofix_svg_margins
    html = _render_for("Testco", monkeypatch, tmp_path, with_logo=True)
    body = html[html.find("<body"):]
    cover = body[body.find("cover-logo"):body.find("cover-eyebrow")]
    assert "client-plate" in cover
    assert "rf-wordmark" not in cover           # redundant text removed
    assert 'data-brand-asset="1"' in cover      # marker injected
    assert 'viewBox="0 0 345 48"' in cover      # native viewBox preserved
    fixed, _ = autofix_svg_margins(html)
    assert 'viewBox="0 0 345 48"' in fixed       # and survives remediation


def test_monogram_fallback_keeps_the_client_name_text(tmp_path, monkeypatch):
    html = _render_for("Testco", monkeypatch, tmp_path, with_logo=False)
    body = html[html.find("<body"):]
    cover = body[body.find("cover-logo"):body.find("cover-eyebrow")]
    assert "rf-wordmark" in cover               # text name beside the monogram
    assert "client-mark-monogram" in cover
    assert "Testco" in cover


def test_identity_lint_contract_holds_both_ways(tmp_path, monkeypatch):
    from agents.reports.lint import lint_brief_identity
    for with_logo in (True, False):
        html = _render_for("Testco", monkeypatch, tmp_path, with_logo=with_logo)
        assert lint_brief_identity(html).ok  # svg in .cover-logo either way


# ── Command Center identity: markup + aspect-aware slots ─────────────────────
def _index_html():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(root, "ui", "index.html"), encoding="utf-8").read()


def test_command_center_drops_gtm_group_text_keeps_product_title():
    html = _index_html()
    assert "GTM GROUP <span>" not in html
    assert 'class="product-title">LILA CONTROL ROOM' in html


def test_command_center_gtm_mark_grown():
    html = _index_html()
    assert re.search(r"\.gtm-mark\{height:3[2-6]px", html)


def test_command_center_uses_aspect_aware_slots():
    html = _index_html()
    # wide header + smaller row slots exist and are aspect-aware (width:auto)
    assert ".clogo-hdr{height:48px;width:auto" in html
    assert ".clogo-row{height:30px;width:auto" in html
    assert "clogo-hdr'" in html   # client-page header uses the wide slot
    assert "clogo-row'" in html   # home rows use the smaller slot
    # square-only clogo-lg presentation retired from the call sites
    assert "clogo-lg'" not in html


def test_command_center_no_remote_fetch_in_primary_logo_path():
    html = _index_html()
    # the primary client logo src is the LOCAL marks endpoint, not a remote host
    assert 'src="/api/marks/client/' in html


# ── Cycle-5 review repairs (2026-07-12): robust parse, mark kind, cross-renderer
# lockup, square rasters, exact marker, responsive rules ─────────────────────

def test_svg_with_xml_prolog_and_single_quotes_is_marked_and_viewbox_kept():
    from agents.reports.capture_brief import _mark_svg_as_brand
    from agents.reports.svg_safety import _is_brand_asset
    raw = ("<?xml version='1.0' encoding='UTF-8'?><!-- vendor mark -->\n"
           "<svg class='logo' viewBox='0 0 300 40'><rect width='300' height='40'/></svg>")
    marked, kind = _mark_svg_as_brand(raw, "Acme Wide")
    assert _is_brand_asset(marked)                       # finding 1: now marked
    assert "viewBox='0 0 300 40'" in marked              # native viewBox intact
    assert marked[:marked.find(">") + 1].count("class=") == 1  # merged, not dup
    assert kind == "wordmark"                            # aspect 7.5 -> wide
    # and the marker survives an autofix pass byte-for-byte
    from agents.reports.svg_safety import autofix_svg_margins
    fixed, _ = autofix_svg_margins(f"<body>{marked}</body>")
    assert "viewBox='0 0 300 40'" in fixed


def test_empty_svg_file_falls_back_to_monogram_with_name(tmp_path, monkeypatch):
    import agents.reports.capture_brief as cb
    empty = tmp_path / "empty.svg"
    empty.write_text("   ")
    monkeypatch.setattr(cb, "_resolve_client_mark_path", lambda n: empty)
    html, kind = cb._resolve_client_mark("Testco")
    assert kind == "monogram"                            # finding 3: agree
    lockup = cb.client_cover_lockup("Testco")
    assert "client-plate" not in lockup                  # no plate for a monogram
    assert "rf-wordmark" in lockup and "Testco" in lockup  # name preserved


def test_real_square_raster_is_not_forced_wide(tmp_path, monkeypatch):
    import agents.reports.capture_brief as cb
    osprey = cb._resolve_client_mark_path("Osprey Flight Solutions")
    assert osprey is not None and osprey.suffix == ".png"  # the committed square
    html, kind = cb._resolve_client_mark("Osprey Flight Solutions")
    assert kind == "square"                              # finding 4: not wide
    assert "client-brand-square" in html
    assert 'viewBox="0 0 128 128"' in html               # native dims, not 160x48
    lockup = cb.client_cover_lockup("Osprey Flight Solutions")
    assert "client-plate" not in lockup and "rf-wordmark" in lockup  # name kept


def test_exact_marker_never_exempts_a_chart():
    from agents.reports.svg_safety import _is_brand_asset, svg_margin_violations
    # finding 5: a chart mentioning the string cannot be exempted
    sneaky = ('<svg aria-label="data-brand-asset" viewBox="0 0 200 100">'
              '<text x="0" y="0" font-size="14">x</text></svg>')
    assert not _is_brand_asset(sneaky)
    assert svg_margin_violations(sneaky)                 # still governed
    assert not _is_brand_asset('<svg data-brand-asset="0"><text x="0" y="0"/></svg>')


def test_shared_cover_lockup_used_by_all_three_renderers():
    import inspect
    from agents.reports import capture_brief, views, target_report
    # finding 2: one helper, consumed everywhere; the old inline lockup is gone
    for mod in (views, target_report):
        src = inspect.getsource(mod)
        assert "client_cover_lockup" in src
        assert 'rf-wordmark">{_esc(' not in src        # inline name lockup retired
    assert "def client_cover_lockup" in inspect.getsource(capture_brief)


def test_wordmark_client_gets_plate_in_the_three_view_renderer(
        tmp_path, monkeypatch):
    import agents.reports.capture_brief as cb
    wide = tmp_path / "wide.svg"
    wide.write_text('<svg viewBox="0 0 345 48"><rect width="345" height="48"/></svg>')
    monkeypatch.setattr(cb, "_resolve_client_mark_path", lambda n: wide)
    from agents.reports.document import build_document
    from agents.reports.views import render_assessment
    from tests.test_assessment_document import _searches
    doc = build_document("Testco", searches=_searches(3), qualify=None)
    html = render_assessment(doc, "client")
    body = html[html.find("<body"):]
    cover = body[body.find("cover-logo"):body.find("cover-eyebrow")]
    assert "client-plate" in cover                       # finding 2: plate here too
    assert "rf-wordmark" not in cover                    # no duplicate name
    assert 'data-brand-asset="1"' in cover
    assert 'viewBox="0 0 345 48"' in cover               # native viewBox intact


def test_command_center_has_narrow_identity_treatment():
    html = _index_html()
    # finding 6/7: the top bar and client header re-flow at a phone breakpoint
    assert re.search(r"@media\s*\(max-width:720px\)", html)
    narrow = html[html.find("@media (max-width:720px)"):]
    narrow = narrow[:narrow.find("@media", 5) if narrow.find("@media", 5) > 0 else 1200]
    assert ".top{flex-wrap:wrap" in narrow
    assert ".chead{flex-wrap:wrap" in narrow
    assert ".clogo-hdr" in narrow                        # wordmark constrained
