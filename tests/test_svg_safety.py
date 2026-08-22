"""R13 SVG render safety: no chart in any LILA report clips.
The regression fixture IS the incident: labels at x=0 on a 0-origin
viewBox must fail; the standard component must pass."""
from agents.reports.capture_brief import CBBudgetBar, _budget_chart
from agents.reports.lint import lint_svg_geometry
from agents.reports.svg_safety import (
    SAFE_INSET, autofix_svg_margins, end_label_placement, est_text_width,
    safe_viewbox, svg_margin_violations,
)

# the exact failure shape that shipped: axis label flush at x=0
CLIPPED = (
    '<svg viewBox="0 0 800 66" xmlns="http://www.w3.org/2000/svg">'
    '<text x="0" y="14" font-size="14" text-anchor="start">NAICS 518210</text>'
    '<rect x="200" y="0" width="540" height="20"></rect>'
    "</svg>")


def test_regression_fixture_fails_lint():
    v = lint_svg_geometry(f"<html><body>{CLIPPED}</body></html>").violations
    assert v and all(x.rule == "svg_geometry" for x in v)
    # both offenders: text at x=0 AND the bar ending flush at 740 < 792 is
    # fine, but the rect starts at y=0 which breaches the top margin
    details = " | ".join(x.detail for x in v)
    assert "text 'NAICS 518210'" in details


def test_autofix_expands_viewbox_and_clears():
    fixed, fixes = autofix_svg_margins(CLIPPED)
    assert fixes and "viewBox expanded" in fixes[0]
    assert not svg_margin_violations(fixed)


def test_component_emits_safe_chart():
    bars = [CBBudgetBar(label="Air Force data platform recompete window",
                        amount_label="$515.3M", relative=1.0),
            CBBudgetBar(label="SOCOM GEOINT", amount_label="$190.7M",
                        relative=0.37)]
    html = _budget_chart(bars)
    assert 'width="100%"' in html          # overflow guard: scales, no fixed px
    assert 'x="0"' not in html and 'y="0"' not in html
    assert not lint_svg_geometry(html).violations


def test_end_label_moves_inside_bar_when_it_would_breach():
    x, anchor, fill = end_label_placement(200, 540, "$1,238,890,340 obligated",
                                          13, 800)
    assert anchor == "end" and fill == "#ffffff"   # inside the bar
    x2, anchor2, fill2 = end_label_placement(200, 100, "$1M", 13, 800)
    assert anchor2 == "start" and x2 == 308        # outside, fits fine


def test_helpers_are_convention_not_hand_tuning():
    assert safe_viewbox(800, 66) == "-12 -12 824 90"
    assert est_text_width("abcd", 10) == 24.8
    assert SAFE_INSET == 8.0
