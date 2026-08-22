"""R13 SVG render safety (2026-07-09): no chart in any LILA report clips.

Root incident: money-in-motion axis labels clipped at the chart's left
border. <text> placed flush against the viewBox edge clips because glyph
side-bearing extends slightly negative. The fix is a STANDARD, not hand
tuning:

1. SAFE MARGINS: no rendered element may begin or end within SAFE_INSET
   (8 units) of any viewBox edge. Chart components get the margin by
   convention: the viewBox starts at a negative inset.
2. TEXT PLACEMENT: left-anchored text x >= 8; end-of-bar value labels are
   width-checked (0.62em per character) and move inside the bar with a
   contrast-safe fill when they would breach the right margin; nothing
   renders at exactly x=0 or y=0.
3. LINT: lint_svg_geometry parses every inline <svg> in rendered HTML and
   flags any element whose estimated bounding box sits inside the margin
   or outside the viewBox.
4. AUTO-FIX: autofix_svg_margins expands the viewBox (a pure padding
   change, layout semantics untouched) where that deterministically clears
   the violation; anything it cannot clear stays for the lint to FLAG.
"""

from __future__ import annotations

import re
from typing import Optional

SAFE_INSET = 8.0          # minimum clearance from every viewBox edge
COMPONENT_INSET = 12.0    # convention inset chart components bake in
CHAR_W = 0.62             # estimated glyph width in em at set font-size


def safe_viewbox(width: float, height: float,
                 inset: float = COMPONENT_INSET) -> str:
    """The component convention: viewBox starts at a negative inset so the
    safety margin exists by construction, never by hand tuning."""
    return (f"{-inset:g} {-inset:g} {width + 2 * inset:g} "
            f"{height + 2 * inset:g}")


def est_text_width(text: str, font_size: float) -> float:
    return CHAR_W * font_size * len(text or "")


def end_label_placement(bar_x: float, bar_w: float, label: str,
                        font_size: float, right_edge: float,
                        pad: float = 8.0) -> tuple[float, str, str]:
    """(x, text-anchor, fill) for an end-of-bar value label. Outside the
    bar when it fits inside the right margin; inside the bar with a
    contrast-safe fill when it would breach."""
    w = est_text_width(label, font_size)
    if bar_x + bar_w + pad + w <= right_edge - SAFE_INSET:
        return bar_x + bar_w + pad, "start", "var(--ink)"
    return bar_x + bar_w - pad, "end", "#ffffff"


_SVG_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.S | re.I)
_VIEWBOX_RE = re.compile(r'viewBox="([-\d.]+)[ ,]+([-\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)"')
_TEXT_RE = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.S | re.I)
_RECT_RE = re.compile(r"<rect\b([^>]*?)/?>", re.I)
_CIRCLE_RE = re.compile(r"<circle\b([^>]*?)/?>", re.I)
_PATH_D_RE = re.compile(r'<path\b[^>]*?\bd="([^"]+)"', re.I)
_NUM_PAIR_RE = re.compile(r"(-?\d+(?:\.\d+)?)[ ,](-?\d+(?:\.\d+)?)")


def _attr(attrs: str, name: str) -> Optional[float]:
    m = re.search(rf'\b{name}="(-?\d+(?:\.\d+)?)"', attrs)
    return float(m.group(1)) if m else None


def _sattr(attrs: str, name: str) -> str:
    m = re.search(rf'\b{name}="([^"]*)"', attrs)
    return m.group(1) if m else ""


def _element_boxes(svg: str) -> list[tuple[str, float, float, float, float]]:
    """(label, x0, y0, x1, y1) per parseable element. Percent or var()
    geometry is skipped: it scales with the container and cannot clip the
    viewBox."""
    boxes = []
    for m in _TEXT_RE.finditer(svg):
        attrs, content = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        x, y = _attr(attrs, "x"), _attr(attrs, "y")
        if x is None or y is None:
            continue
        size = _attr(attrs, "font-size") or 13.0
        w = est_text_width(content, size)
        anchor = _sattr(attrs, "text-anchor") or "start"
        x0 = x - w if anchor == "end" else (x - w / 2 if anchor == "middle" else x)
        boxes.append((f"text '{content[:24]}'", x0, y - size, x0 + w, y))
    for m in _RECT_RE.finditer(svg):
        a = m.group(1)
        x, y = _attr(a, "x") or 0.0, _attr(a, "y") or 0.0
        w, h = _attr(a, "width"), _attr(a, "height")
        if w is None or h is None:
            continue
        boxes.append(("rect", x, y, x + w, y + h))
    for m in _CIRCLE_RE.finditer(svg):
        a = m.group(1)
        cx, cy, r = _attr(a, "cx"), _attr(a, "cy"), _attr(a, "r")
        if cx is None or cy is None or r is None:
            continue
        boxes.append(("circle", cx - r, cy - r, cx + r, cy + r))
    for m in _PATH_D_RE.finditer(svg):
        pts = [(float(a), float(b)) for a, b in _NUM_PAIR_RE.findall(m.group(1))]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            boxes.append(("path", min(xs), min(ys), max(xs), max(ys)))
    return boxes


_BRAND_ASSET_RE = re.compile(r"""data-brand-asset\s*=\s*['"]1['"]""", re.I)


def _is_brand_asset(svg: str) -> bool:
    """True when the svg's OPENING tag carries the exact imported-brand marker
    data-brand-asset="1". Checked on the opening tag only, and requires the
    exact attribute VALUE (Cycle-5 review) so neither a chart mentioning the
    string in content, an aria-label of that text, nor data-brand-asset="0"
    can accidentally exempt a generated chart from R13."""
    head = svg[:svg.find(">") + 1] if ">" in svg else svg
    return bool(_BRAND_ASSET_RE.search(head))


def svg_margin_violations(svg: str) -> list[str]:
    """R13 rule 3: any element whose bounding box falls within SAFE_INSET
    of a viewBox edge, or extends past it.

    Imported brand assets (client wordmarks/logos) are EXEMPT (2026-07-12):
    they carry their own native geometry and must never be reshaped by
    chart-oriented remediation (the NETSCOUT wide wordmark was clipped when
    R13 rewrote its viewBox). Generated chart SVGs carry no such marker and
    stay fully governed. This one exemption covers both the lint and the
    autofix, because autofix_svg_margins skips any svg with no violations."""
    if _is_brand_asset(svg):
        return []
    vb = _VIEWBOX_RE.search(svg)
    if not vb:
        return []
    vx, vy, vw, vh = (float(vb.group(i)) for i in range(1, 5))
    lo_x, lo_y = vx + SAFE_INSET, vy + SAFE_INSET
    hi_x, hi_y = vx + vw - SAFE_INSET, vy + vh - SAFE_INSET
    out = []
    for label, x0, y0, x1, y1 in _element_boxes(svg):
        if x0 < lo_x or y0 < lo_y or x1 > hi_x or y1 > hi_y:
            out.append(f"{label} bbox ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) "
                       f"breaches the {SAFE_INSET:g}-unit margin of "
                       f"viewBox {vb.group(0)}")
    return out


def autofix_svg_margins(html: str) -> tuple[str, list[str]]:
    """AUTO-FIX where deterministic: expand the viewBox so every element
    box clears the margin. Pure padding, layout semantics untouched. Any
    svg without parseable geometry is left for the lint to FLAG."""
    fixes: list[str] = []

    def _fix(m: re.Match) -> str:
        svg = m.group(0)
        if not svg_margin_violations(svg):
            return svg
        vb = _VIEWBOX_RE.search(svg)
        boxes = _element_boxes(svg)
        if not vb or not boxes:
            return svg
        vx, vy, vw, vh = (float(vb.group(i)) for i in range(1, 5))
        x0 = min(min(b[1] for b in boxes) - SAFE_INSET, vx)
        y0 = min(min(b[2] for b in boxes) - SAFE_INSET, vy)
        x1 = max(max(b[3] for b in boxes) + SAFE_INSET, vx + vw)
        y1 = max(max(b[4] for b in boxes) + SAFE_INSET, vy + vh)
        new_vb = f'viewBox="{x0:g} {y0:g} {x1 - x0:g} {y1 - y0:g}"'
        fixes.append(f"viewBox expanded {vb.group(0)} -> {new_vb}")
        return svg.replace(vb.group(0), new_vb, 1)

    return _SVG_RE.sub(_fix, html), fixes
