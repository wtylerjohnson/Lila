"""The Federal Market Map document shell: the format the operator approved.

WHY THIS FILE EXISTS. The Market Map design - a 130-class stylesheet, a
runtime with an evidence drawer, a serif editorial layout on warm paper -
was approved as "perfect" and then lived NOWHERE except a downloaded HTML
file in someone's browser folder. The repo could not reproduce it. So a press
fell back to the `sb-*` assessment chrome, the renderer emitted a private
class vocabulary that chrome had never heard of, and the result was 29,000
characters of unstyled HTML with run-on navigation and colliding ticker text.

A design that is not in the codebase is not a design. It is a screenshot.

WHAT THIS SHELL PROVIDES.

  studio-toolbar    edit, resize text, save, reset, download, print
  ticker            linked marquee of live records, duplicated for a seamless loop
  brand-band        client logo slot, report lockup, GTM mark
  memo-title        serif display title and the edition stamp
  coverage-strip    the at-a-glance answer row, where figures open their receipt
  pipeline-nav      numbered section breadcrumb plus the show-your-work control
  [content]         the seven sections, supplied by the renderer
  report-foot       closing statement and footer mark
  work-drawer       the evidence panel every figure opens

THREE RULES THE SHELL ENFORCES.

1 EVERY EMITTED CLASS IS STYLED. `style_contract.require_styled` runs over
  the finished document. The unstyled artifact passed every other test in
  the suite; this is the one that would have stopped it.

2 NO CLIENT'S DATA REACHES ANOTHER CLIENT'S DOCUMENT. The source artifact
  carried a 775KB `embeddedState` block holding one client's saved edits and
  32 embedded images. It is never read and never shipped. Identity marks are
  resolved from the current client's local cache and the curated agency
  catalog; an unresolved identity renders a visible monogram fallback.

3 FIGURES ARE NOT EDITABLE, PROSE IS. A figure resolves to a linked source
  record, so a hand-edited figure would silently contradict its own
  evidence. Amounts render as work triggers; only prose carries
  `data-edit-id`.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

MARKET_MAP_SKELETON_VERSION = "market_map_skeleton.v1.2026-08-07"

_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "market_map"

# An empty slot the operator fills by drag, paste or double click. A client
# artifact never ships another client's mark.
EMPTY_SLOT = ("data:image/gif;base64,"
              "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

_DATA_MARK = "/* __MARKET_MAP_DATA__ */"


class SkeletonError(RuntimeError):
    """The shell could not be assembled from its assets."""


def esc(value: Any) -> str:
    return html.escape(" ".join(str(value or "").split()), quote=True)


def load_css() -> str:
    path = _ASSETS / "market_map.css"
    if not path.exists():
        raise SkeletonError(f"the Market Map stylesheet is missing at {path}")
    return path.read_text(encoding="utf-8")


def load_runtime() -> str:
    path = _ASSETS / "market_map_runtime.js"
    if not path.exists():
        raise SkeletonError(f"the Market Map runtime is missing at {path}")
    text = path.read_text(encoding="utf-8")
    if _DATA_MARK not in text:
        raise SkeletonError(
            "the runtime has no data injection point; the per-report ledgers "
            "and drawer entries would have nowhere to go")
    return text


class EditIds:
    """Sequential editable field ids, unique by construction.

    The approved artifact numbers its fields mm-001, mm-002 and so on rather
    than naming them semantically. Sequence is what makes them unique, and
    the Studio compiler rejects a duplicate.
    """

    def __init__(self, prefix: str = "mm") -> None:
        self.prefix = prefix
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"{self.prefix}-{self._n:03d}"

    @property
    def issued(self) -> int:
        return self._n


def editable(tag: str, text: Any, ids: EditIds, *, cls: str = "",
             resizable: bool = True) -> str:
    """A prose element the operator may revise in the delivered file."""
    attrs = f' class="{cls}"' if cls else ""
    attrs += f' data-edit-id="{ids.next()}"'
    if resizable:
        attrs += ' data-resizable-text=""'
    return f"<{tag}{attrs}>{esc(text)}</{tag}>"


def _mark_fallback(label: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(label or ""))
    if not words:
        return "?"
    meaningful = [w for w in words if w.casefold() not in {
        "the", "of", "and", "department", "office", "united", "states",
    }]
    words = meaningful or words
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(word[0] for word in words[:3]).upper()


def logo_slot(slot_id: str, label: str, *, cls: str = "",
              width: int = 0, height: int = 0, required: bool = True,
              src: str = "", fallback: str = "") -> str:
    """A portable identity mark with an explicit, editable fallback.

    ``src`` is accepted only when it is already an image data URI.  Renderers
    never fetch: the asset resolver must populate the local cache before this
    deterministic step.  Unresolved slots omit the image node entirely so a
    transparent pixel can never masquerade as a mark; Studio drag/drop targets
    the containing slot and creates the image when supplied.
    """
    style = (f' style="width:{width}px;height:{height}px"'
             if width and height else "")
    safe_src = str(src or "").strip()
    has_image = safe_src.startswith("data:image/")
    classes = ("logo-slot " + cls
               + (" has-image" if has_image else " is-unmatched")).strip()
    fallback_text = str(fallback or "").strip() or _mark_fallback(label)
    image = (f'<img data-portable-image="" src="{safe_src}" '
             f'alt="{esc(label)}">' if has_image else "")
    return (
        f'<div class="{classes}" data-logo-slot-id="{esc(slot_id)}"'
        + (' data-required-logo="true"' if required else "")
        + f' tabindex="0" aria-label="{esc(label)} logo slot"'
        f' title="Edit mode: drop, paste, double-click or resize this logo"'
        f'{style}>'
        f'{image}'
        f'<span class="logo-fallback" aria-hidden="true">'
        f'{esc(fallback_text)}</span>'
        f"</div>")


def ticker(items: Any) -> str:
    """The linked marquee.

    THE GROUP IS DUPLICATED ON PURPOSE. The animation translates the track by
    exactly one group width, so a single group would scroll off and leave a
    visible gap before it looped. Two identical groups make the loop seamless.
    """
    rows = []
    for item in (items or []):
        label = esc(item.get("label"))
        url = str(item.get("url") or "").strip()
        rows.append(
            f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">'
            f"{label}</a>" if url else f"<a>{label}</a>")
    if not rows:
        return ""
    group = '<div class="ticker-group">' + "".join(rows) + "</div>"
    return ('<div class="ticker" aria-label="Federal market ticker">'
            '<div class="ticker-track">' + group + group + "</div></div>")


TOOLBAR = (
    '<div class="studio-toolbar" role="toolbar" aria-label="Report editing tools">'
    '<button type="button" data-action="toggle-edit" aria-pressed="false">'
    "Edit report</button>"
    '<button type="button" data-action="text-smaller" '
    'title="Resize selected editable text">Text −</button>'
    '<button type="button" data-action="text-larger" '
    'title="Resize selected editable text">Text +</button>'
    '<button type="button" data-action="save-browser">Save in browser</button>'
    '<button type="button" data-action="reset">Reset</button>'
    '<button class="primary" type="button" data-action="download">'
    "Save HTML version</button>"
    '<button type="button" data-action="print">Print / PDF</button>'
    '<span class="studio-status" data-report-status="" aria-live="polite">'
    "Preparing portable HTML</span></div>")

DRAWER = (
    '<div class="drawer-scrim" id="workDrawer" role="dialog" aria-modal="true"'
    ' aria-labelledby="drawerTitle"><aside class="work-drawer">'
    '<header class="drawer-head"><div><span>Show your work</span>'
    '<h2 id="drawerTitle">Evidence and calculation</h2></div>'
    '<button class="drawer-close" type="button" data-action="close-work"'
    ' aria-label="Close evidence drawer">×</button></header>'
    '<div class="drawer-body">'
    '<div class="drawer-label">What this claim means</div>'
    '<p id="workSummary">Select any amount, decision or source row to see the '
    "evidence behind it.</p>"
    '<div class="drawer-label">Math or promotion rule</div>'
    '<div class="drawer-formula" id="workFormula">No claim selected.</div>'
    '<div class="drawer-label">Primary sources</div>'
    '<div class="drawer-sources" id="workSources"></div></div>'
    '<footer class="drawer-foot"><span id="workSelected">'
    "Selected claim: full report</span>"
    '<button type="button" data-action="close-work">Done</button>'
    "</footer></aside></div>")


def pipeline_nav(sections: Any) -> str:
    """The numbered breadcrumb, with the show-your-work control."""
    parts = []
    for index, (sid, label) in enumerate(sections, start=1):
        if index > 1:
            parts.append("<span>›</span>")
        parts.append(f'<a href="#{esc(sid)}">{index} · {esc(label)}</a>')
    return ('<nav class="pipeline-nav" aria-label="Report sections">'
            "<strong>Read the market map</strong>" + "".join(parts)
            + '<button class="show-work-link" type="button" '
              'data-action="toggle-work">Show your work</button></nav>')


def work_data(details: Any) -> str:
    """The runtime's per-report data block, generated rather than hand-written.

    In the source artifact this was a hand-authored JavaScript literal with
    one client's ledgers and dollar figures baked in. Here it is generated
    from the projection, so the drawer content and the rendered figures come
    from the same numbers and cannot drift apart.
    """
    payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
    # </script> inside a JSON string would close the block early.
    payload = payload.replace("</", "<\\/")
    return f"    const workDetails = {payload};"


def build_document(*, client_name: str, slug: str, stamp: str,
                   title: str, standfirst: str, edition: str,
                   edition_note: str, coverage: Any, sections: Any,
                   content: str, ticker_items: Any = (),
                   work_details: Any = None, footer_note: str = "",
                   ids: Any = None) -> str:
    """Assemble the complete, self-contained Market Map document."""
    ids = ids or EditIds()
    display = " ".join(str(client_name).split())
    underscored = re.sub(r"[^A-Za-z0-9]+", "_", display).strip("_")
    report_id = f"{slug}-federal-market-map-{stamp}"
    # Local-only identity resolution. The live intake/press path may hydrate
    # these caches; replay consumes exactly what is already on disk.
    from agents.reports.report_assets import client_logo, gtm_logo
    client_mark = client_logo(display)
    gtm_mark = gtm_logo()

    coverage_html = []
    for item in (coverage or []):
        label, value, note = (item.get("label"), item.get("value"),
                              item.get("note"))
        work = str(item.get("work") or "").strip()
        extra = " needs" if item.get("needs") else ""
        # A FIGURE OPENS ITS RECEIPT; a word is prose the operator may edit.
        value_html = (
            f'<button class="coverage-value work-trigger" type="button" '
            f'data-work="{esc(work)}">{esc(value)}</button>' if work
            else f'<strong data-edit-id="{ids.next()}">{esc(value)}</strong>')
        coverage_html.append(
            f'<div class="coverage-item{extra}"><span>{esc(label)}</span>'
            f'{value_html}<small data-edit-id="{ids.next()}">{esc(note)}'
            f"</small></div>")

    runtime = load_runtime().replace(_DATA_MARK, work_data(work_details))

    return (
        "<!doctype html>\n"
        f'<html lang="en" data-report-id="{esc(report_id)}" '
        f'data-download-name="{esc(underscored)}_Federal_Market_Map_'
        f'{esc(stamp)}.html">\n'
        "<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(display)} · Federal Market Map</title>\n"
        f'<meta name="description" content="{esc(display)} federal market map, '
        f'{esc(stamp)}.">\n'
        f"<style>\n{load_css()}\n</style>\n</head>\n<body>\n"
        + TOOLBAR
        + ticker(ticker_items)
        + '<main class="memo">'
        + '<header class="brand-band" id="overview"><div class="brand-lockup">'
        + logo_slot(f"{slug}-header-logo", display, cls="client-mark",
                    src=client_mark)
        + '<div class="brand-copy">'
        + editable("strong", "Federal Market Map", ids, resizable=False)
        + editable("span", "Company · spend · competition · "
                           "opportunities · teaming · contacts",
                   ids, resizable=False)
        + "</div></div>"
        + logo_slot("gtm-header-logo", "GTM Strategies", cls="gtm-mark",
                    src=gtm_mark, fallback="GTM")
        + "</header>"
        + '<section class="memo-title clear-title"><div>'
        + editable("h1", title, ids)
        + editable("p", standfirst, ids)
        + '</div><div class="edition-stamp">'
        + editable("strong", edition, ids, resizable=False)
        + editable("span", edition_note, ids, resizable=False)
        + "</div></section>"
        + '<section class="coverage-strip" aria-label="Research coverage">'
        + "".join(coverage_html) + "</section>"
        + pipeline_nav(sections)
        + content
        + '<footer class="report-foot">'
        + editable("p", footer_note or (
            "Federal Market Map. One opportunity, one row. Every amount, "
            "identifier and date stays linked to the record it came from."),
            ids, resizable=False)
        + logo_slot("gtm-footer-logo", "GTM Strategies", width=70, height=42,
                    required=False, src=gtm_mark, fallback="GTM")
        + "</footer></main>"
        + DRAWER
        + f"<script>\n{runtime}\n</script>\n</body>\n</html>\n")
