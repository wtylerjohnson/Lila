"""Deterministic renderer and validator for LILA's eight-slot product."""

from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urlsplit

from agents.golden_press.external_product_contract import load_external_product_slots
from agents.golden_press.external_product_projection import (
    ExternalProductDocument,
    record_ownership_key,
)
from agents.golden_press.release_snapshot import canonical_slot_sha256


EXTERNAL_PRODUCT_RENDER_VERSION = "lila-eight-slot-render.v10.2026-08-25"

_LOCAL_PATH = re.compile(
    r"(?:file://|(?:^|[\"'\s(=:])/(?:users|home)/)", re.I | re.M)

_EXECUTABLE_HTML_TAGS = frozenset({
    "applet", "base", "embed", "foreignobject", "frame", "frameset",
    "iframe", "link", "object", "portal", "script",
})
_SVG_MUTATION_TAGS = frozenset({
    "animate", "animatemotion", "animatetransform", "set",
})
_URL_ATTRIBUTES = frozenset({
    "action", "background", "cite", "data", "formaction", "href", "ping",
    "poster", "src", "srcset", "xlink:href", "xml:base",
})
_CSS_VALUE_ATTRIBUTES = frozenset({
    "clip-path", "cursor", "fill", "filter", "marker-end", "marker-mid",
    "marker-start", "mask", "stroke", "style",
})
_CSS_URL = re.compile(r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_HEX_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})[\t\n\f\r ]?")
_CSS_SIMPLE_ESCAPE = re.compile(r"\\([^\n\r\f])")


def _compact_active_value(value: Any) -> str:
    decoded = html.unescape(str(value or ""))
    return re.sub(r"[\x00-\x20\x7f]+", "", decoded).casefold()


def _safe_html_url(tag: str, attribute: str, value: str) -> bool:
    raw = html.unescape(str(value or "")).strip()
    if not raw:
        return True
    compact = _compact_active_value(raw)
    if attribute in {"href", "xlink:href"} and compact.startswith("#"):
        return True
    if attribute == "src" and tag == "img":
        return compact.startswith("data:image/") and "," in raw
    if attribute not in {"href", "xlink:href"} or tag not in {"a", "area"}:
        return False
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return False
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def _active_css_reason(value: str) -> str:
    css = _CSS_COMMENT.sub("", html.unescape(str(value or "")))
    css = _CSS_HEX_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), css)
    css = _CSS_SIMPLE_ESCAPE.sub(lambda match: match.group(1), css)
    compact = _compact_active_value(css)
    for token in (
            "@import", "expression(", "javascript:", "vbscript:",
            "-moz-binding", "image-set("):
        if token in compact:
            return token.rstrip("(:")
    if re.search(r"(?:^|[;{])behavior:", compact):
        return "behavior"
    for match in _CSS_URL.finditer(css):
        target = html.unescape(match.group(2)).strip()
        compact_target = _compact_active_value(target)
        if (compact_target.startswith("#")
                or compact_target.startswith("data:image/")):
            continue
        return "non-local CSS URL"
    return ""


class _ExternalProductHtmlSafetyParser(HTMLParser):
    """Reject active HTML while preserving the product's static visuals."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[dict] = []
        self._seen: set[tuple[str, str]] = set()
        self._style_depth = 0

    def _add(self, rule: str, detail: str) -> None:
        key = (rule, detail)
        if key in self._seen:
            return
        self._seen.add(key)
        self.violations.append({"rule": rule, "detail": detail})

    def _inspect_tag(self, tag: str,
                     attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in _EXECUTABLE_HTML_TAGS:
            rule = ("script_in_client_artifact" if tag == "script"
                    else "executable_tag_in_client_artifact")
            self._add(rule, f"the client artifact contains <{tag}>")
        if tag in _SVG_MUTATION_TAGS:
            self._add("active_svg_mutation_in_client_artifact",
                      f"the client artifact contains active SVG <{tag}>")
        values = [(str(name).casefold(), "" if value is None else str(value))
                  for name, value in attrs]
        attr_map = dict(values)
        if (tag == "meta"
                and attr_map.get("http-equiv", "").strip().casefold() == "refresh"):
            self._add("meta_refresh_in_client_artifact",
                      "the client artifact contains meta refresh")
        for name, value in values:
            if name.startswith("on"):
                self._add("event_handler_in_client_artifact",
                          f"the client artifact contains the {name} handler")
            if name == "srcdoc":
                self._add("srcdoc_in_client_artifact",
                          "the client artifact contains srcdoc")
            if name in _URL_ATTRIBUTES and not _safe_html_url(tag, name, value):
                self._add("active_url_in_client_artifact",
                          f"the client artifact contains an unsafe {name} URL")
            if name in _CSS_VALUE_ATTRIBUTES:
                reason = _active_css_reason(value)
                if reason:
                    self._add("active_css_in_client_artifact",
                              f"the client artifact contains active CSS: {reason}")

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_tag(tag, attrs)
        if tag.casefold() == "style":
            self._style_depth += 1

    def handle_startendtag(self, tag: str,
                           attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._style_depth:
            return
        reason = _active_css_reason(data)
        if reason:
            self._add("active_css_in_client_artifact",
                      f"the client artifact contains active CSS: {reason}")


def _external_html_safety_violations(client_html: str) -> list[dict]:
    parser = _ExternalProductHtmlSafetyParser()
    try:
        parser.feed(client_html)
        parser.close()
    except (AssertionError, ValueError) as exc:
        parser._add("invalid_client_html",
                    f"the client artifact could not be parsed: {type(exc).__name__}")
    return parser.violations


class _IdentitySurfaceParser(HTMLParser):
    """Collect typed record identity surfaces from the rendered client HTML."""

    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.surfaces: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._depth = 0
        self._fallback_depth: int | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {str(key).casefold(): "" if value is None else str(value)
                  for key, value in attrs}
        classes = set(values.get("class", "").split())
        tag = tag.casefold()
        if self._current is None:
            if tag == "div" and "product-identity-mark" in classes:
                self._current = {
                    "attrs": values, "images": [], "fallback": [],
                }
                self._depth = 1
            return
        if tag == "img":
            self._current["images"].append(values)
        if tag not in self._VOID:
            self._depth += 1
            if tag == "span" and "logo-fallback" in classes:
                self._fallback_depth = self._depth

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if self._current is not None and tag.casefold() not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None or tag.casefold() in self._VOID:
            return
        if self._fallback_depth == self._depth:
            self._fallback_depth = None
        self._depth -= 1
        if self._depth == 0:
            self.surfaces.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._fallback_depth is not None:
            self._current["fallback"].append(data)


def _identity_surfaces(client_html: str) -> list[dict[str, Any]]:
    parser = _IdentitySurfaceParser()
    try:
        parser.feed(client_html)
        parser.close()
    except (AssertionError, ValueError):
        return []
    return parser.surfaces


_PRODUCT_CSS = r"""
.product-css-sentinel{display:contents}
.product-slot{scroll-margin-top:32px}
.product-slot-intro{min-width:0}
.product-slot-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:0 0 22px}
.product-metric{border:1px solid var(--line);background:var(--paper-deep);padding:15px;min-width:0}
.product-metric span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.09em}
.product-metric strong{display:block;margin-top:7px;font-family:var(--mono);font-size:19px;overflow-wrap:anywhere}
.product-metric small{display:block;margin-top:6px;color:var(--muted);line-height:1.45}
.product-records{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
.product-ledger{display:block;margin-top:18px;border:1px solid var(--line);background:var(--paper-deep)}
.product-ledger>summary{cursor:pointer;padding:14px 16px;color:var(--ink);font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase}
.product-ledger>summary span{color:var(--muted);font-family:var(--mono);font-weight:400}
.product-ledger-body{padding:0 16px 16px}
.product-ledger-note{margin:0 0 13px;color:var(--muted);font-size:12px;line-height:1.5}
.product-review-queue{margin-top:22px;padding-top:17px;border-top:2px solid var(--line-strong)}
.product-review-queue>summary{font-size:14px}
.product-review-records{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
.product-review-record{border-style:dashed;background:var(--paper-deep)}
.product-review-record[data-decision-state="out_of_scope"]{opacity:.82}
.product-decision-state{display:inline-block;margin:0 0 8px;padding:4px 7px;border:1px solid var(--accent);color:var(--accent);font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.product-review-reasons{margin:12px 0 0;padding:0;list-style:none}
.product-review-reasons li{margin:6px 0;padding-left:12px;border-left:2px solid var(--accent);font-size:11px;line-height:1.45}
.product-decision-action{margin-top:12px;padding:10px;background:var(--paper);font-size:11px;line-height:1.5}
.product-review-evidence{margin-top:12px;padding-top:10px;border-top:1px solid var(--line)}
.product-review-evidence>summary{cursor:pointer;color:var(--accent);font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.product-priority-group{margin-top:17px}
.product-priority-group>h3{margin:0 0 10px;font-size:13px;letter-spacing:.06em;text-transform:uppercase}
.product-priority-decisions{margin-top:18px}
.product-priority-decision{grid-template-columns:70px minmax(0,1fr)}
.product-action-order.product-decision-badge{width:62px;font-size:9px;text-transform:uppercase}
.product-slot[data-slot-id="research-mesh"] .product-records,.product-slot[data-slot-id="industry-days-events"] .product-records{grid-template-columns:repeat(3,minmax(0,1fr))}
.product-record{border:1px solid var(--line);background:var(--paper);padding:17px;min-width:0;break-inside:avoid}
.product-identity-mark{display:grid;grid-template-columns:44px minmax(0,1fr);gap:10px;align-items:center;margin:0 0 12px;padding:0 0 11px;border-bottom:1px solid var(--line)}
.product-identity-mark .product-agency-seal.logo-slot{width:42px;height:42px;min-width:42px;border:0;border-radius:50%;background:transparent;padding:0;overflow:hidden}
.product-identity-mark .product-agency-seal.logo-slot img{width:100%;height:100%;object-fit:contain;display:block}
.product-identity-mark .product-agency-seal.logo-slot .logo-fallback{display:flex;width:100%;height:100%;align-items:center;justify-content:center;border:1px solid var(--line-strong);border-radius:50%;background:var(--paper-deep);color:var(--ink);font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:.04em}
.product-identity-mark .product-agency-seal.logo-slot.has-image .logo-fallback{display:none}
.product-identity-copy{min-width:0}
.product-identity-copy span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.09em}
.product-identity-copy strong{display:block;margin-top:3px;font-size:11px;line-height:1.3;overflow-wrap:anywhere}
.product-record-kind{display:block;color:var(--accent);font-size:10px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:7px}
.product-record h3{font-size:17px;line-height:1.3;margin:0 0 8px;overflow-wrap:anywhere}
.product-record p{color:var(--muted);font-size:13px;line-height:1.55;margin:7px 0}
.product-fields{display:grid;grid-template-columns:minmax(92px,.42fr) minmax(0,1fr);gap:7px 12px;margin:12px 0 0}
.product-fields dt{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.07em}
.product-fields dd{margin:0;font-size:12px;overflow-wrap:anywhere}
.product-source{display:inline-block;margin-top:12px;color:var(--link);font-family:var(--mono);font-size:11px;overflow-wrap:anywhere}
.product-priority{display:grid;grid-template-columns:42px minmax(0,1fr);gap:13px}
.product-action-order{display:flex;align-items:center;justify-content:center;width:34px;height:34px;border:1px solid var(--accent);color:var(--accent);font-family:var(--mono);font-weight:700}
.product-targets{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.product-targets h4{margin:0 0 9px;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.product-target{border-left:2px solid var(--accent);padding:7px 9px;margin:7px 0;background:var(--paper-deep);font-size:11px;line-height:1.45}
.product-target strong{display:block;font-size:12px}
.product-detail{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.product-detail summary{cursor:pointer;color:var(--accent);font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.product-detail-section{margin-top:12px}
.product-detail-section h4,.product-receipts h4{margin:0 0 8px;font-size:10px;letter-spacing:.07em;text-transform:uppercase}
.product-detail-section .product-fields{margin-top:0}
.product-receipts{margin-top:13px;padding-top:11px;border-top:1px solid var(--line)}
.product-receipt{display:block;margin:6px 0;color:var(--link);font-family:var(--mono);font-size:10px;overflow-wrap:anywhere}
.product-gap{margin:15px 0 0;border:1px dashed var(--accent);padding:14px;color:var(--muted);font-size:13px;line-height:1.55}
.product-visuals{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:18px}
.product-visual{border:1px solid var(--line);background:var(--paper-deep);padding:14px;min-width:0}
.product-visual h3{font-size:13px;margin:0 0 10px}
.product-visual p{color:var(--muted);font-size:11px;line-height:1.45;margin:0 0 12px}
.product-svg{display:block;width:100%;height:auto;overflow:visible}
.product-svg-axis{stroke:var(--line-strong);stroke-width:1}
.product-svg-path{fill:none;stroke:var(--accent);stroke-width:2}
.product-svg-point{fill:var(--accent);stroke:var(--paper);stroke-width:2}
.product-svg-bar{fill:var(--accent)}
.product-svg-label{fill:var(--ink);font:9px var(--mono)}
.product-svg-edge{stroke:var(--line-strong);stroke-width:1.2;marker-end:url(#productArrow)}
.product-svg-node{fill:var(--paper);stroke:var(--accent);stroke-width:1.2}
.direction-graph{min-height:180px}
.vector-map{min-height:180px}
.product-reference{color:var(--muted);font-size:11px;font-family:var(--mono);margin-top:10px}
.product-status{white-space:nowrap}
@media(max-width:980px){.product-slot[data-slot-id="research-mesh"] .product-records,.product-slot[data-slot-id="industry-days-events"] .product-records{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.product-records,.product-review-records,.product-slot[data-slot-id="research-mesh"] .product-records,.product-slot[data-slot-id="industry-days-events"] .product-records,.product-visuals{grid-template-columns:1fr}.product-slot-metrics{grid-template-columns:1fr 1fr}.product-fields{grid-template-columns:1fr}.product-fields dd{margin-bottom:5px}}
@page{size:letter;margin:.42in}
@media print{
html,body,.memo{background:#fff!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.memo{box-shadow:none}
.product-slot{padding-top:28px;padding-bottom:10px}
.product-slot-gap{padding-top:18px;padding-bottom:6px;break-inside:avoid;page-break-inside:avoid}
.product-slot-intro{break-inside:avoid;page-break-inside:avoid;break-after:avoid;page-break-after:avoid}
.product-slot .plain-head{margin-bottom:14px;padding-bottom:11px;break-after:avoid;page-break-after:avoid}
.product-slot-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-bottom:14px;break-inside:avoid;page-break-inside:avoid}
.product-metric{padding:11px}
.product-records{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;align-items:start}
.product-review-queue{break-before:auto;page-break-before:auto;break-inside:auto;page-break-inside:auto;margin-top:16px;padding-top:12px}
.product-review-queue>h3,.product-review-queue>p{break-after:avoid;page-break-after:avoid}
.product-review-records{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;align-items:start}
.product-slot[data-slot-id="research-mesh"] .product-records,.product-slot[data-slot-id="industry-days-events"] .product-records{grid-template-columns:repeat(2,minmax(0,1fr))}
.product-slot[data-slot-id="priority-pursuits"] .product-records,.product-slot[data-slot-id="federal-opportunities"] .product-records{grid-template-columns:1fr}
.product-slot[data-slot-id="federal-opportunities"] .product-review-records{grid-template-columns:1fr}
.product-record{margin:0;break-inside:avoid;page-break-inside:avoid;padding:13px;overflow:hidden}
.product-identity-mark{grid-template-columns:36px minmax(0,1fr);gap:8px;margin-bottom:9px;padding-bottom:8px}
.product-identity-mark .product-agency-seal.logo-slot{width:34px;height:34px;min-width:34px}
.product-detail,.product-review-evidence{display:none!important}
.product-research-ledger>.product-ledger-body,.product-review-queue:not([open])>.product-ledger-body{display:none!important}
.product-ledger>summary{padding:10px 12px;break-inside:avoid;page-break-inside:avoid}
.product-review-queue[open]>.product-ledger-body{padding:0 0 10px}
.product-review-record{break-inside:auto;page-break-inside:auto;overflow:visible}
.product-fields{gap:5px 9px;margin-top:9px}
.product-source{color:#000;text-decoration:underline;overflow-wrap:anywhere;word-break:break-word}
.product-gap{margin-top:8px;padding:11px;break-inside:avoid;page-break-inside:avoid}
.product-visuals{display:block;margin-top:12px}
.product-visual{margin:0 0 10px;padding:12px;break-inside:avoid;page-break-inside:avoid;overflow:hidden}
.product-svg{max-height:180px}
.report-foot{break-inside:avoid;page-break-inside:avoid;margin-top:24px}
}
"""


def esc(value: Any) -> str:
    return html.escape(" ".join(str(value or "").split()), quote=True)


def _http(value: Any) -> str:
    url = " ".join(str(value or "").split())
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "Not stated"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _source_link(record: dict) -> str:
    url = _http(record.get("source_url"))
    if not url:
        return ""
    label = record.get("source_id") or "Open source record"
    if _http(label):
        label = "Open source record"
    return (f'<a class="product-source" href="{esc(url)}" target="_blank" '
            f'rel="noopener noreferrer">{esc(label)} ↗</a>')


def _fields(record: dict) -> str:
    labels = (
        ("agency", "Agency"), ("sub_agency", "Sub-agency"),
        ("office", "Office"),
        ("recipient", "Recipient"), ("response_date", "Timing"),
        ("value", "Published value"), ("commercial_route", "Route"),
        ("route_action", "Pursuit path"),
        ("window_state", "Window"), ("service_fit", "Fit"),
        ("naics", "NAICS"), ("psc", "PSC"),
        ("instrument", "Instrument"), ("set_aside", "Set-aside"),
        ("solicitation_number", "Solicitation number"),
        ("role", "Relationship"), ("target_role", "Target role"),
        ("person", "Named person"), ("date", "Date"),
        ("location", "Location"), ("status", "Status"),
        ("method", "Method"), ("result_count", "Returned"),
        ("kept_after_screen", "Kept"),
    )
    rows = []
    for key, label in labels:
        value = record.get(key)
        if value in (None, "", [], {}):
            continue
        rows.append(f"<dt>{esc(label)}</dt><dd>{esc(_display_value(value))}</dd>")
    return ('<dl class="product-fields">' + "".join(rows) + "</dl>"
            if rows else "")


def _detail_sections(record: dict) -> str:
    sections = []
    for section in record.get("detail_sections") or []:
        rows = []
        for field in section.get("fields") or []:
            value = field.get("value")
            if value in (None, "", [], {}):
                continue
            rows.append(
                f'<dt>{esc(field.get("label"))}</dt>'
                f'<dd>{esc(_display_value(value))}</dd>')
        if rows:
            sections.append(
                '<section class="product-detail-section">'
                f'<h4>{esc(section.get("heading"))}</h4>'
                '<dl class="product-fields">' + "".join(rows) + "</dl></section>")
    if not sections:
        return ""
    return ('<details class="product-detail">'
            '<summary>Evidence and decision detail</summary>'
            + "".join(sections) + "</details>")


def _receipt_links(record: dict) -> str:
    links = []
    for receipt in record.get("evidence_receipts") or []:
        url = _http(receipt.get("source_url"))
        if not url:
            continue
        label = receipt.get("label") or receipt.get("source_id") or "Source record"
        links.append(
            f'<a class="product-receipt" href="{esc(url)}" target="_blank" '
            f'rel="noopener noreferrer">{esc(label)} ↗</a>')
    if not links:
        return ""
    return ('<div class="product-receipts"><h4>Evidence receipts</h4>'
            + "".join(links) + "</div>")


def _targets(record: dict) -> str:
    rows = []
    for target in record.get("targets") or []:
        name = target.get("name") or target.get("role_needed") or target.get("role")
        organization = target.get("organization") or ""
        source_kind = target.get("source_kind") or "target"
        contact = target.get("email") or target.get("phone") or ""
        rows.append(
            '<div class="product-target">'
            f'<span>{esc(source_kind)}</span><strong>{esc(name)}</strong>'
            f'<span>{esc(organization)}</span>'
            + (f'<span> · {esc(contact)}</span>' if contact else "")
            + "</div>")
    if not rows:
        return '<div class="product-gap">Target research next: resolve the named roles for this opportunity without substituting a generic inbox or switchboard.</div>'
    return ('<div class="product-targets"><h4>Targets specific to this opportunity</h4>'
            + "".join(rows) + "</div>")


def _identity_labels(record: Mapping[str, Any]) -> tuple[str, ...]:
    labels: list[str] = []
    for key in ("sub_agency", "agency"):
        label = " ".join(str(record.get(key) or "").split())
        if label and label not in labels:
            labels.append(label)
    return tuple(labels)


def _brand_key(client_name: str, kind: str, label: str) -> str:
    from agents.reports.signal_board_presentation import logo_identity

    return logo_identity(client_name, kind, label)


def _freeze_identity_mark(target: dict[str, str], key: str, src: str) -> None:
    if src and not str(src).startswith("data:image/"):
        raise ValueError(f"external product identity mark {key!r} is not portable")
    previous = target.get(key) if key in target else None
    if previous is not None and previous != src:
        raise ValueError(f"external product identity mark {key!r} is ambiguous")
    target[key] = src


def capture_external_product_identity_assets(
    doc: ExternalProductDocument,
) -> dict[str, dict[str, str]]:
    """Resolve every visible record identity before the pure release compile.

    Federal records use the curated agency resolver and then the committed
    packaged seal catalog. Event organizers are treated as organizations unless
    the strict federal resolver recognizes them; this prevents names such as
    ``Navy League`` from inheriting a military seal by word association.
    """
    from agents.golden_press.report_templates import find_mark
    from agents.reports import report_assets

    agency_marks: dict[str, str] = {}
    organization_marks: dict[str, str] = {}

    def catalog_src(label: str, *, kind: str) -> str:
        entry = find_mark(label, kind=kind)
        return str((entry or {}).get("src") or "")

    for slot in doc.slots:
        for record in tuple(slot.records) + tuple(slot.review_records):
            labels = _identity_labels(record)
            if not labels:
                continue
            is_event = str(record.get("kind") or "").casefold() == "event"
            if is_event:
                federal = any(report_assets._seal_key(label) for label in labels)
                if federal:
                    for label in labels:
                        strict_key = report_assets._seal_key(label)
                        if not strict_key:
                            continue
                        src = (report_assets.agency_seal(label, doc.client_name)
                               or catalog_src(label, kind="agency"))
                        _freeze_identity_mark(
                            agency_marks,
                            _brand_key(doc.client_name, "agency", label),
                            src,
                        )
                    continue
                for label in labels:
                    src = (report_assets.company_logo(label, doc.client_name)
                           or catalog_src(label, kind="org"))
                    _freeze_identity_mark(
                        organization_marks,
                        _brand_key(doc.client_name, "company", label),
                        src,
                    )
                continue
            for label in labels:
                src = (report_assets.agency_seal(label, doc.client_name)
                       or catalog_src(label, kind="agency"))
                _freeze_identity_mark(
                    agency_marks,
                    _brand_key(doc.client_name, "agency", label),
                    src,
                )
    return {
        "agency_marks": dict(sorted(agency_marks.items())),
        "organization_marks": dict(sorted(organization_marks.items())),
    }


def _frozen_identity_maps(
    render_assets: Any,
) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(render_assets, Mapping):
        return {}, {}
    agency = render_assets.get("agency_marks") or {}
    organizations = render_assets.get("organization_marks") or {}
    return (
        {str(key): str(value) for key, value in agency.items()}
        if isinstance(agency, Mapping) else {},
        {str(key): str(value) for key, value in organizations.items()}
        if isinstance(organizations, Mapping) else {},
    )


def _record_identity(
    record: Mapping[str, Any], *, client_name: str,
    agency_marks: Mapping[str, str],
    organization_marks: Mapping[str, str],
) -> tuple[str, str, str, str] | None:
    labels = _identity_labels(record)
    if not labels:
        return None
    is_event = str(record.get("kind") or "").casefold() == "event"
    if is_event:
        for label in labels:
            key = _brand_key(client_name, "agency", label)
            if agency_marks.get(key):
                return "agency", label, key, str(agency_marks[key])
        for label in labels:
            key = _brand_key(client_name, "company", label)
            if organization_marks.get(key):
                return "company", label, key, str(organization_marks[key])
        for label in labels:
            key = _brand_key(client_name, "agency", label)
            if key in agency_marks:
                return "agency", label, key, ""
        for label in labels:
            key = _brand_key(client_name, "company", label)
            if key in organization_marks:
                return "company", label, key, ""
        from agents.reports.report_assets import _seal_key
        for label in labels:
            if _seal_key(label):
                return "agency", label, _brand_key(
                    client_name, "agency", label), ""
        label = labels[0]
        return "company", label, _brand_key(client_name, "company", label), ""
    for label in labels:
        key = _brand_key(client_name, "agency", label)
        if agency_marks.get(key):
            return "agency", label, key, str(agency_marks[key])
    for label in labels:
        key = _brand_key(client_name, "agency", label)
        if key in agency_marks:
            return "agency", label, key, ""
    label = labels[0]
    return "agency", label, _brand_key(client_name, "agency", label), ""


def _identity_surface_key(
    slot_id: str, record: Mapping[str, Any], index: int, *, review: bool = False,
) -> str:
    role = "review" if review else "record"
    reference = record.get("reference_key") or record.get("record_key")
    digest = hashlib.sha256(
        str(reference or "anonymous").encode("utf-8")).hexdigest()[:16]
    return f"{slot_id}:{role}:{index}:{digest}"


def _identity_mark(
    record: Mapping[str, Any], *, client_name: str,
    agency_marks: Mapping[str, str],
    organization_marks: Mapping[str, str], surface_key: str,
) -> str:
    identity = _record_identity(
        record, client_name=client_name, agency_marks=agency_marks,
        organization_marks=organization_marks)
    if identity is None:
        return ""
    kind, label, key, src = identity
    from agents.golden_press.market_map_skeleton import logo_slot

    slot = logo_slot(
        f"product-identity-{surface_key}", label,
        cls="product-agency-seal",
        required=False, src=src,
    )
    label_kind = "Federal authority" if kind == "agency" else "Organization"
    state = "seal" if src else "fallback"
    return (
        f'<div class="product-identity-mark" '
        f'data-product-identity-key="{esc(surface_key)}" '
        f'data-brand-kind="{esc(kind)}" data-brand-key="{esc(key)}" '
        f'data-brand-label="{esc(label)}" data-identity-mark="{state}"'
        + (' data-agency-seal="true"' if kind == "agency" and src else "")
        + f'>{slot}<div class="product-identity-copy">'
        f'<span>{esc(label_kind)}</span><strong>{esc(label)}</strong>'
        '</div></div>')


def _priority(
    record: dict, *, client_name: str = "",
    agency_marks: Mapping[str, str] | None = None,
    organization_marks: Mapping[str, str] | None = None,
    surface_key: str = "",
) -> str:
    fields = []
    for label, key in (("Route", "route"),
                       ("Pursuit path", "route_action"),
                       ("Timing", "timing"),
                       ("Next action", "next_action"),
                       ("Targets", "target_count")):
        value = record.get(key)
        if value not in (None, ""):
            fields.append(f"<dt>{esc(label)}</dt><dd>{esc(_display_value(value))}</dd>")
    kind = record.get("reference_kind")
    label = {
        "decision_required": "Decision required",
        "forecast_action": "Forecast action",
        "qualified_action": "Actionable pursuit",
    }.get(kind, "Deadline-ordered action")
    decision_required = kind == "decision_required"
    badge = ("Review" if decision_required else
             f"{int(record.get('action_order') or 0):02d}")
    classes = ("product-record product-priority product-priority-decision"
               if decision_required else "product-record product-priority")
    owner_label = {
        "federal-opportunities": "Federal Opportunities Identified",
        "future-forecasts": "Future Forecasts",
    }.get(record.get("reference_slot_id"), "its evidence slot")
    return (
        f'<article class="{classes}">'
        f'<div class="product-action-order{" product-decision-badge" if decision_required else ""}">{esc(badge)}</div>'
        '<div>'
        + _identity_mark(
            record, client_name=client_name,
            agency_marks=agency_marks or {},
            organization_marks=organization_marks or {},
            surface_key=surface_key,
        )
        + f'<span class="product-record-kind">{esc(label)}</span>'
        f'<h3>{esc(record.get("title"))}</h3>'
        f'<p>{esc(record.get("why"))}</p>'
        + ('<dl class="product-fields">' + "".join(fields) + "</dl>" if fields else "")
        + f'<div class="product-reference">Full evidence appears in {esc(owner_label)}.</div>'
        + "</div></article>")


def _record(
    record: dict, *, include_targets: bool = False, review: bool = False,
    client_name: str = "", agency_marks: Mapping[str, str] | None = None,
    organization_marks: Mapping[str, str] | None = None,
    surface_key: str = "",
) -> str:
    if "reference_key" in record:
        return _priority(
            record, client_name=client_name, agency_marks=agency_marks,
            organization_marks=organization_marks, surface_key=surface_key)
    summary = record.get("summary") or record.get("next_action") or ""
    if (" ".join(str(summary).split()).casefold()
            == " ".join(str(record.get("title") or "").split()).casefold()):
        summary = ""
    classes = "product-record product-review-record" if review else "product-record"
    decision_state = record.get("decision_state") or "needs_review"
    reasons = "".join(
        f"<li>{esc(reason)}</li>" for reason in record.get("reasons") or [])
    decision = (
        f'<span class="product-decision-state">{esc(decision_state.replace("_", " "))}</span>'
        + ('<ul class="product-review-reasons">' + reasons + '</ul>'
           if reasons else "")
        + (f'<div class="product-decision-action"><strong>Decision action:</strong> '
           f'{esc(record.get("decision_action"))}</div>'
           if record.get("decision_action") else "")
        if review else ""
    )
    evidence = (
        '<details class="product-review-evidence">'
        '<summary>Full evidence record</summary>'
        + _fields(record)
        + _receipt_links(record)
        + _detail_sections(record)
        + '</details>'
        if review else (
            _fields(record) + _receipt_links(record) + _detail_sections(record))
    )
    return (
        f'<article class="{classes}" '
        f'data-record-key="{esc(record.get("record_key"))}" '
        f'data-decision-state="{esc(decision_state if review else "")}">'
        + decision
        + _identity_mark(
            record, client_name=client_name,
            agency_marks=agency_marks or {},
            organization_marks=organization_marks or {},
            surface_key=surface_key,
        )
        + f'<span class="product-record-kind">{esc(record.get("kind"))}</span>'
        + f'<h3>{esc(record.get("title"))}</h3>'
        + (f'<p>{esc(summary)}</p>' if summary else "")
        + _source_link(record)
        + evidence
        + (_targets(record) if include_targets else "")
        + "</article>")


def _review_queue(
    slot: Any, *, client_name: str = "",
    agency_marks: Mapping[str, str] | None = None,
    organization_marks: Mapping[str, str] | None = None,
) -> str:
    if not slot.review_records:
        return ""
    opportunity = slot.slot_id == "federal-opportunities"
    heading = ("Needs review before pursuit" if opportunity
               else "Held outside qualified totals")
    note = (
        "These current notices remain visible with the exact evidence decision "
        "that blocks promotion. They are not pursuit recommendations."
        if opportunity else
        "These records remain visible for adjudication or context, but their "
        "figures are excluded from qualified totals."
    )
    records = "".join(
        _record(
            row, review=True, include_targets=opportunity,
            client_name=client_name, agency_marks=agency_marks,
            organization_marks=organization_marks,
            surface_key=_identity_surface_key(
                slot.slot_id, row, index, review=True),
        )
        for index, row in enumerate(slot.review_records)
    )
    open_attribute = " open" if opportunity else ""
    review_count = sum(
        row.get("decision_state") == "needs_review"
        for row in slot.review_records)
    context_count = sum(
        row.get("decision_state") == "out_of_scope"
        for row in slot.review_records)
    count_label = (
        f"{len(slot.review_records)} record(s)"
        if opportunity else
        f"{review_count} review; {context_count} context"
    )
    return (
        f'<details class="product-ledger product-review-queue"{open_attribute}>'
        f'<summary>{esc(heading)} <span>· {esc(count_label)}</span></summary>'
        '<div class="product-ledger-body">'
        f'<p class="product-ledger-note">{esc(note)}</p>'
        f'<div class="product-review-records">{records}</div></div></details>'
    )


def _priority_groups(
    records: Any, *, slot_id: str, client_name: str,
    agency_marks: Mapping[str, str],
    organization_marks: Mapping[str, str],
) -> str:
    indexed = list(enumerate(records))
    qualified = [(index, row) for index, row in indexed
                 if row.get("reference_kind") != "decision_required"]
    decisions = [(index, row) for index, row in indexed
                 if row.get("reference_kind") == "decision_required"]
    sections = []
    if qualified:
        sections.append(
            '<div class="product-priority-group">'
            '<h3>Qualified actions</h3>'
            '<div class="product-records">'
            + "".join(_priority(
                row, client_name=client_name, agency_marks=agency_marks,
                organization_marks=organization_marks,
                surface_key=_identity_surface_key(slot_id, row, index))
                      for index, row in qualified)
            + '</div></div>')
    if decisions:
        sections.append(
            '<details class="product-ledger product-priority-decisions" open>'
            f'<summary>Decisions to resolve <span>· {len(decisions)} record(s)</span></summary>'
            '<div class="product-ledger-body product-records">'
            + "".join(_priority(
                row, client_name=client_name, agency_marks=agency_marks,
                organization_marks=organization_marks,
                surface_key=_identity_surface_key(slot_id, row, index))
                      for index, row in decisions)
            + '</div></details>')
    return "".join(sections)


def _record_ledger(
    slot: Any, records: str, *, client_name: str,
    agency_marks: Mapping[str, str],
    organization_marks: Mapping[str, str],
) -> str:
    if not records:
        return ""
    if slot.slot_id == "priority-pursuits":
        return _priority_groups(
            slot.records, slot_id=slot.slot_id, client_name=client_name,
            agency_marks=agency_marks,
            organization_marks=organization_marks)
    if slot.slot_id == "research-mesh":
        query_count = sum(
            row.get("kind") == "research_query" for row in slot.records)
        lane_count = sum(
            row.get("kind") == "source_lane" for row in slot.records)
        return (
            '<details class="product-ledger product-research-ledger">'
            f'<summary>Research execution ledger <span>· {query_count} queries + {lane_count} source lanes</span></summary>'
            '<div class="product-ledger-body">'
            '<p class="product-ledger-note">Every query body, execution time, method, result count, and retained count remains inspectable here.</p>'
            f'<div class="product-records">{records}</div></div></details>')
    return '<div class="product-records">' + records + "</div>"


def _metrics(metrics: Any) -> str:
    cards = []
    for metric in metrics or ():
        cards.append(
            '<article class="product-metric">'
            f'<span>{esc(metric.get("label"))}</span>'
            f'<strong>{esc(_display_value(metric.get("value")))}</strong>'
            f'<small>{esc(metric.get("note"))}</small></article>')
    return ('<div class="product-slot-metrics">' + "".join(cards) + "</div>"
            if cards else "")


def _directional_graph(visual: dict) -> str:
    points = list(visual.get("points") or [])[:12]
    if not points:
        return ""
    width, height = 620, 230
    left, top, right, bottom = 40, 30, 25, 45
    usable_w, usable_h = width - left - right, height - top - bottom
    values = []
    for point in points:
        value = point.get("magnitude")
        values.append(float(value) if isinstance(value, (int, float)) else 0.0)
    high = max(values) or 1.0
    bars = []
    bar_space = usable_w / max(1, len(points))
    bar_width = max(8.0, min(34.0, bar_space * .58))
    for index, (point, value) in enumerate(zip(points, values)):
        x = left + bar_space * index + bar_space / 2
        bar_height = max(2.0, (value / high) * usable_h)
        y = top + usable_h - bar_height
        label = f"F{index + 1}"
        bars.append(
            f'<rect class="product-svg-bar" x="{x - bar_width / 2:.1f}" '
            f'y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" />'
            f'<text class="product-svg-label" x="{x:.1f}" y="{height - 18}" '
            f'text-anchor="middle">{label}</text>')
    return (
        f'<svg class="product-svg direction-graph" viewBox="0 0 {width} {height}" role="img" aria-label="Published forecast value floor chart">'
        f'<line class="product-svg-axis" x1="{left}" y1="{top + usable_h}" x2="{width-right}" y2="{top + usable_h}" />'
        + "".join(bars) + "</svg>")


def _vector_map(visual: dict) -> str:
    edges = list(visual.get("edges") or [])[:24]
    if not edges:
        return ""
    root = str(edges[0].get("from") or "Client")
    agencies = []
    for edge in edges:
        if edge.get("from") == root and edge.get("to") not in agencies:
            agencies.append(edge.get("to"))
    agencies = agencies[:8]
    width, height = 620, max(220, 70 + len(agencies) * 34)
    root_x, root_y = 90, height / 2
    items = [
        '<defs><marker id="productArrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="currentColor" /></marker></defs>',
        f'<circle class="product-svg-node" cx="{root_x}" cy="{root_y:.1f}" r="28" />',
        f'<text class="product-svg-label" x="{root_x}" y="{root_y + 3:.1f}" text-anchor="middle">{esc(root[:14])}</text>',
    ]
    for index, agency in enumerate(agencies):
        y = 40 + index * ((height - 80) / max(1, len(agencies) - 1))
        x = 430
        items.append(
            f'<line class="product-svg-edge" x1="{root_x+30}" y1="{root_y:.1f}" x2="{x-58}" y2="{y:.1f}" />'
            f'<rect class="product-svg-node" x="{x-48}" y="{y-15:.1f}" width="180" height="30" rx="4" />'
            f'<text class="product-svg-label" x="{x-36}" y="{y+3:.1f}" text-anchor="start">{esc(str(agency)[:30])}</text>')
    return (f'<svg class="product-svg vector-map" viewBox="0 0 {width} {height}" role="img" aria-label="Forecast relationship vector map">'
            + "".join(items) + "</svg>")


def _visuals(visuals: Any) -> str:
    cards = []
    for visual in visuals or ():
        body = (_directional_graph(visual)
                if visual.get("type") == "directional_graph"
                else _vector_map(visual)
                if visual.get("type") == "relationship_vector_map" else "")
        if body:
            cards.append('<article class="product-visual">'
                         f'<h3>{esc(visual.get("title"))}</h3>'
                         + (f'<p>{esc(visual.get("note"))}</p>'
                            if visual.get("note") else "")
                         + body + '</article>')
    return ('<div class="product-visuals">' + "".join(cards) + "</div>"
            if cards else "")


def render_slot(
    slot: Any, *, client_name: str = "",
    agency_marks: Mapping[str, str] | None = None,
    organization_marks: Mapping[str, str] | None = None,
) -> str:
    agency_marks = agency_marks or {}
    organization_marks = organization_marks or {}
    include_targets = slot.slot_id in {
        "federal-opportunities", "teaming-opportunities"}
    records = "".join(
        _record(
            row, include_targets=include_targets,
            client_name=client_name, agency_marks=agency_marks,
            organization_marks=organization_marks,
            surface_key=_identity_surface_key(slot.slot_id, row, index),
        )
        for index, row in enumerate(slot.records)
    )
    gaps = "".join(f'<div class="product-gap">{esc(gap)}</div>'
                   for gap in slot.gaps)
    status_class = " product-slot-gap" if slot.status == "gap" else ""
    return (
        f'<section class="plain-section product-slot{status_class}" id="{esc(slot.slot_id)}" '
        f'data-slot-id="{esc(slot.slot_id)}" data-slot-number="{slot.number}">'
        '<div class="product-slot-intro">'
        '<div class="plain-head">'
        f'<span class="plain-number">{slot.number}</span><div>'
        f'<h2>{esc(slot.heading)}</h2><p>{esc(slot.summary)}</p></div>'
        f'<span class="plain-state product-status">{esc(slot.status)}</span></div>'
        + _metrics(slot.metrics)
        + '</div>'
        + _record_ledger(
            slot, records, client_name=client_name,
            agency_marks=agency_marks,
            organization_marks=organization_marks)
        + _review_queue(
            slot, client_name=client_name, agency_marks=agency_marks,
            organization_marks=organization_marks)
        + _visuals(slot.visuals) + gaps + "</section>")


def _work_details(doc: ExternalProductDocument) -> dict:
    details = {}
    for slot in doc.slots:
        sources = []
        for record in tuple(slot.records) + tuple(slot.review_records):
            url = _http(record.get("source_url"))
            if url:
                if slot.slot_id == "research-mesh":
                    host = urlsplit(url).netloc.casefold()
                    label = (
                        "USAspending search API endpoint; exact query is in Slot 2"
                        if host.endswith("usaspending.gov") else
                        record.get("title") or "Research source endpoint"
                    )
                else:
                    label = record.get("source_id") or record.get("title")
                if _http(label):
                    label = "Open source record"
                sources.append([label, url])
        formulas = []
        for metric in slot.metrics:
            formula = str(metric.get("formula") or "").strip()
            if formula:
                formulas.append(f"{metric.get('label')}: {formula}")
            for receipt in metric.get("evidence") or []:
                url = _http(receipt.get("source_url") or receipt.get("url"))
                if not url:
                    continue
                label = (receipt.get("label") or receipt.get("source_id")
                         or "Source record")
                sources.append([label, url])
        unique_sources = []
        seen_sources = set()
        for label, url in sources:
            key = url if slot.slot_id == "research-mesh" else (str(label), url)
            if key in seen_sources:
                continue
            seen_sources.add(key)
            unique_sources.append([label, url])
        if slot.slot_id == "priority-pursuits":
            qualified = sum(
                row.get("reference_kind") != "decision_required"
                for row in slot.records)
            decisions = sum(
                row.get("reference_kind") == "decision_required"
                for row in slot.records)
            population = (
                f"{qualified} qualified action reference(s); "
                f"{decisions} decision-queue reference(s); "
                f"{len(slot.gaps)} named gap(s)"
            )
        elif slot.slot_id == "research-mesh":
            queries = sum(
                row.get("kind") == "research_query" for row in slot.records)
            lanes = sum(
                row.get("kind") == "source_lane" for row in slot.records)
            population = (
                f"{queries} exact query receipt(s); {lanes} source-lane "
                f"receipt(s); {len(slot.gaps)} named gap(s)"
            )
        else:
            population = (
                f"{len(slot.records)} qualified/owned record(s); "
                f"{len(slot.review_records)} non-qualified record(s); "
                f"{len(slot.gaps)} named gap(s)"
            )
            review_required = slot.coverage.get("review_required")
            out_of_scope = slot.coverage.get("out_of_scope")
            if review_required is not None or out_of_scope is not None:
                population += (
                    f" ({int(review_required or 0)} review-required; "
                    f"{int(out_of_scope or 0)} out-of-scope)"
                )
        details[f"slot-{slot.number}"] = {
            "title": slot.heading,
            "summary": slot.summary,
            "formula": "; ".join(formulas + [population]) + ".",
            "sources": unique_sources,
        }
    return details


def _coverage(doc: ExternalProductDocument) -> list[dict]:
    by_id = {slot.slot_id: slot for slot in doc.slots}
    opportunities = by_id["federal-opportunities"]
    targets = sum(len(row.get("targets") or []) for row in opportunities.records)
    pursuit_actions = sum(
        row.get("reference_kind") != "decision_required"
        for row in by_id["priority-pursuits"].records)
    prime = sum(row.get("route_action") == "prime"
                for row in opportunities.records)
    team = sum(row.get("route_action") == "team"
               for row in opportunities.records)
    verify = sum(row.get("route_action") == "verify"
                 for row in opportunities.records)
    return [
        {"label": "Product slots", "value": "8 / 8",
         "note": "Operator-locked order", "work": "slot-1"},
        {"label": "Deadline-ordered actions",
         "value": pursuit_actions,
         "note": "Pursuit actions; fit decisions are a separate queue", "work": "slot-1"},
        {"label": "Direct-fit pursuits", "value": len(opportunities.records),
         "note": f"{prime} prime; {team} team; {verify} verify route",
         "work": "slot-5"},
        {"label": "Opportunities needing review",
         "value": len(opportunities.review_records),
         "note": "Visible with exact blockers", "work": "slot-5"},
        {"label": "Opportunity targets", "value": targets,
         "note": "Bound beneath their opportunity", "work": "slot-5"},
    ]


def _ticker(doc: ExternalProductDocument) -> list[dict]:
    slot = next(item for item in doc.slots if item.slot_id == "priority-pursuits")
    return [{
        "label": (
            ("Decision queue · " if row.get("reference_kind") == "decision_required"
             else f"{int(row.get('action_order') or 0):02d} · ")
            + f"{row.get('title')}"
        ),
        "url": "",
    } for row in slot.records]


def render_external_product(
    doc: ExternalProductDocument, *, render_assets: Any = None,
) -> tuple[str, str]:
    """Return editable studio HTML and the scriptless client artifact."""
    from agents.golden_press.market_map_press import _client_export
    from agents.golden_press.market_map_skeleton import EditIds, build_document

    sections = [(slot.slot_id, slot.heading) for slot in doc.slots]
    ids = EditIds(prefix="lila")
    if render_assets is None:
        identity_assets = capture_external_product_identity_assets(doc)
        agency_marks = identity_assets["agency_marks"]
        organization_marks = identity_assets["organization_marks"]
    else:
        agency_marks, organization_marks = _frozen_identity_maps(render_assets)
    content = "".join(render_slot(
        slot, client_name=doc.client_name, agency_marks=agency_marks,
        organization_marks=organization_marks) for slot in doc.slots)
    details = _work_details(doc)
    classification_as_of = (
        doc.source_receipt.get("classification_as_of") or doc.as_of)
    studio = build_document(
        client_name=doc.client_name, slug=doc.slug, stamp=doc.as_of,
        title="LILA Federal Market Map",
        standfirst=("Eight permanent product slots filled from the governed "
                    "research mesh and Federal Pursuit Graph."),
        edition=f"{doc.client_name} · Complete LILA edition",
        edition_note=(f"Evidence classified {classification_as_of}. Every record remains "
                      "bound to its owning slot and source evidence."),
        coverage=_coverage(doc), sections=sections, content=content,
        ticker_items=_ticker(doc), work_details=details,
        footer_note=("LILA Federal Market Map. Eight fixed slots, one owning "
                     "slot per record, and named gaps instead of silent blanks."),
        ids=ids, render_assets=render_assets,
    )
    studio = studio.replace("</style>", _PRODUCT_CSS + "\n</style>", 1)
    client = _client_export(studio, receipts=details)
    return studio, client


def validate_external_product_document(
    doc: ExternalProductDocument, *, contract_slots: Any = None,
) -> list[dict]:
    violations: list[dict] = []
    contract = (
        load_external_product_slots() if contract_slots is None
        else tuple(contract_slots))
    expected = [(
        int(slot.get("number") if isinstance(slot, dict) else slot.number),
        str(slot.get("slot_id") if isinstance(slot, dict) else slot.slot_id),
        str(slot.get("heading") if isinstance(slot, dict) else slot.heading),
    ) for slot in contract]
    actual = [(slot.number, slot.slot_id, slot.heading) for slot in doc.slots]
    if actual != expected:
        violations.append({"rule": "slot_contract", "detail":
                           f"slot sequence {actual!r} does not match the operator lock"})
    if canonical_slot_sha256(contract) != doc.contract_sha256:
        violations.append({"rule": "slot_contract_digest", "detail":
                           "slot rows do not match the product contract digest"})
    owned: dict[str, str] = {}
    owned_rows: dict[str, dict] = {}
    for slot in doc.slots:
        if slot.status not in {"populated", "gap"}:
            violations.append({"rule": "slot_status", "detail":
                               f"{slot.slot_id} has invalid status {slot.status!r}"})
        if slot.status == "gap" and not slot.gaps:
            violations.append({"rule": "silent_gap", "detail":
                               f"{slot.slot_id} is empty without a named next action"})
        for row in tuple(slot.records) + tuple(slot.review_records):
            key = record_ownership_key(row)
            url = row.get("source_url")
            if url and not _http(url):
                violations.append({"rule": "invalid_source_url", "detail":
                                   f"{key or row.get('record_key')} has a non-HTTP(S) source URL"})
            if row.get("non_owning_reference") is True:
                if (slot.slot_id != "teaming-opportunities"
                        or row.get("kind") != "teaming_route_reference"
                        or not row.get("record_key")
                        or not row.get("source_id")
                        or not row.get("commercial_route")
                        or not row.get("notice_reference_key")
                        or row.get("notice_reference_slot_id") !=
                        "federal-opportunities"):
                    violations.append({
                        "rule": "invalid_non_owning_reference",
                        "detail": (f"{row.get('record_key')!r} is not a complete "
                                   "Slot 6 notice reference"),
                    })
            if not key:
                continue  # Slot 1 references, it does not own evidence rows.
            if key in owned:
                violations.append({"rule": "duplicate_record_owner", "detail":
                                   f"{key} appears in {owned[key]} and {slot.slot_id}"})
            owned[key] = slot.slot_id
            owned_rows[key] = row
        for row in slot.review_records:
            key = record_ownership_key(row)
            if (not str(row.get("decision_state") or "").strip()
                    or row.get("decision_state") == "qualified"):
                violations.append({"rule": "review_decision_state", "detail":
                                   f"{key} has no typed review decision"})
            if not row.get("reason_codes") or not row.get("blocking_dimensions"):
                violations.append({"rule": "review_reason", "detail":
                                   f"{key} has no reason codes or blocking dimensions"})
            if not row.get("reasons") or not row.get("decision_action"):
                violations.append({"rule": "review_action", "detail":
                                   f"{key} has no evidence reason or decision action"})
        if slot.slot_id == "federal-opportunities":
            for row in tuple(slot.records) + tuple(slot.review_records):
                family = row.get("requirement_family")
                for target in row.get("targets") or []:
                    if target.get("requirement_family") not in {None, "", family}:
                        violations.append({"rule": "target_lineage", "detail":
                                           f"target for {target.get('requirement_family')} is nested under {family}"})
                    if target.get("target_slot_id") not in {
                            None, "", "federal-opportunities"}:
                        violations.append({"rule": "target_slot", "detail":
                                           f"target for {family} is nested in the wrong slot"})
            visible_current = sorted({
                str(row.get("source_id") or "")
                for row in tuple(slot.records) + tuple(slot.review_records)
                if row.get("source_id")
            })
            expected_current = sorted(
                doc.graph_receipt.get("current_opportunity_ids") or [])
            if visible_current != expected_current:
                violations.append({
                    "rule": "current_opportunity_conservation",
                    "detail": (f"slot 5 current ids {visible_current!r} do not "
                               f"match graph ids {expected_current!r}"),
                })
        if slot.slot_id == "teaming-opportunities":
            for row in slot.records:
                for target in row.get("targets") or []:
                    if target.get("target_slot_id") != "teaming-opportunities":
                        violations.append({"rule": "target_slot", "detail":
                                           "teaming target is nested in the wrong slot"})
    for row in doc.slots[0].records:
        reference = row.get("reference_key")
        reference_slot = row.get("reference_slot_id")
        if reference not in owned or owned.get(reference) != reference_slot:
            violations.append({"rule": "priority_reference", "detail":
                               f"{reference!r} does not resolve to {reference_slot!r}"})
            continue
        target = owned_rows[reference]
        target_state = str(target.get("decision_state") or "qualified")
        reference_state = str(row.get("decision_state") or "")
        reference_kind = row.get("reference_kind")
        if reference_state != target_state:
            violations.append({"rule": "decision_reference_state", "detail":
                               f"{reference!r} copies {reference_state!r} but its owned record is {target_state!r}"})
        if reference_kind == "decision_required" and target_state == "qualified":
            violations.append({"rule": "decision_reference_state", "detail":
                               f"{reference!r} is typed decision_required but resolves to a qualified record"})
        elif reference_kind == "qualified_action" and target_state != "qualified":
            violations.append({"rule": "decision_reference_state", "detail":
                               f"{reference!r} is typed qualified_action but resolves to {target_state!r}"})
        elif reference_kind == "forecast_action" and target.get("kind") != "forecast":
            violations.append({"rule": "decision_reference_state", "detail":
                               f"{reference!r} is typed forecast_action but does not resolve to a forecast"})
        elif reference_kind not in {
                "decision_required", "qualified_action", "forecast_action"}:
            violations.append({"rule": "decision_reference_state", "detail":
                               f"{reference!r} has unsupported reference kind {reference_kind!r}"})
    teaming_slot = next(
        (slot for slot in doc.slots
         if slot.slot_id == "teaming-opportunities"), None)
    for row in tuple(teaming_slot.records if teaming_slot else ()):
        reference = row.get("notice_reference_key")
        if not reference:
            if row.get("non_owning_reference") is True:
                violations.append({"rule": "teaming_reference", "detail":
                                   "non-owning teaming reference has no notice reference"})
            continue
        if row.get("non_owning_reference") is not True:
            violations.append({"rule": "teaming_reference", "detail":
                               "a notice-linked teaming route claims a second owner"})
        reference_slot = row.get("notice_reference_slot_id")
        if reference not in owned or owned.get(reference) != reference_slot:
            violations.append({"rule": "teaming_reference", "detail":
                               f"{reference!r} does not resolve to {reference_slot!r}"})
            continue
        notice = owned_rows[reference]
        if row.get("source_id") != notice.get("source_id"):
            violations.append({"rule": "teaming_reference", "detail":
                               "teaming route references a different notice identity"})
        if row.get("commercial_route") != notice.get("commercial_route"):
            violations.append({"rule": "teaming_reference", "detail":
                               "teaming route differs from the notice route decision"})
    if not doc.graph_receipt.get("current_opportunity_conserved", False):
        violations.append({"rule": "current_opportunity_conservation", "detail":
                           "the projection receipt reports a lost current opportunity"})
    if not doc.graph_receipt.get("pursuit_membership_conserved", False):
        violations.append({"rule": "pursuit_membership_conservation", "detail":
                           "the product pursuit set differs from the shipped graph qualification set"})
    if doc.graph_receipt.get("orphan_held_opportunity_ids"):
        violations.append({"rule": "orphan_held_opportunity", "detail":
                           "held opportunity ids do not resolve to graph records"})
    if not doc.graph_receipt.get("certified"):
        violations.append({"rule": "graph_not_certified", "detail":
                           "the Federal Pursuit Graph contract is not certified"})
    workflow = doc.graph_receipt.get("workflow_contract") or {}
    if workflow and workflow.get("certified") is False:
        violations.append({"rule": "workflow_not_certified", "detail":
                           "the graph workflow contract is not certified"})
    text = json.dumps(doc.to_dict(), ensure_ascii=False)
    if "—" in text:
        violations.append({"rule": "em_dash", "detail":
                           "an em dash appears in output-reachable product data"})
    if _LOCAL_PATH.search(text):
        violations.append({"rule": "local_path", "detail":
                           "a local filesystem path appears in external product data"})
    return violations


def _validate_identity_contract(
    client_html: str, doc: ExternalProductDocument, *, render_assets: Any,
) -> tuple[list[dict], dict[str, int]]:
    from agents.reports.report_assets import _seal_key

    violations: list[dict] = []
    surfaces = _identity_surfaces(client_html)
    by_key: dict[str, list[dict[str, Any]]] = {}
    for surface in surfaces:
        key = str(surface.get("attrs", {}).get(
            "data-product-identity-key") or "")
        by_key.setdefault(key, []).append(surface)
    agency_marks, organization_marks = _frozen_identity_maps(render_assets)
    expected_keys: set[str] = set()
    expected = 0
    priority = 0
    embedded = 0
    fallbacks = 0

    for slot in doc.slots:
        groups = ((slot.records, False), (slot.review_records, True))
        for records, review in groups:
            for index, record in enumerate(records):
                labels = _identity_labels(record)
                if not labels:
                    continue
                expected += 1
                if slot.slot_id == "priority-pursuits":
                    priority += 1
                surface_key = _identity_surface_key(
                    slot.slot_id, record, index, review=review)
                expected_keys.add(surface_key)
                found = by_key.get(surface_key) or []
                if len(found) != 1:
                    violations.append({
                        "rule": "record_identity_surface",
                        "detail": (f"{surface_key} renders {len(found)} identity "
                                   "surfaces; expected exactly one"),
                    })
                    continue
                surface = found[0]
                attrs = surface["attrs"]
                is_event = str(record.get("kind") or "").casefold() == "event"
                expected_kind = (
                    "agency" if not is_event or any(_seal_key(label)
                                                    for label in labels)
                    else "company")
                actual_kind = attrs.get("data-brand-kind")
                if actual_kind != expected_kind:
                    violations.append({
                        "rule": "record_identity_kind",
                        "detail": (f"{surface_key} renders {actual_kind!r}; "
                                   f"expected {expected_kind!r}"),
                    })
                allowed_keys = {
                    _brand_key(doc.client_name, expected_kind, label)
                    for label in labels
                }
                actual_key = attrs.get("data-brand-key") or ""
                if actual_key not in allowed_keys:
                    violations.append({
                        "rule": "record_identity_key",
                        "detail": (f"{surface_key} renders {actual_key!r}; "
                                   f"expected one of {sorted(allowed_keys)!r}"),
                    })
                if attrs.get("data-brand-label") not in labels:
                    violations.append({
                        "rule": "record_identity_label",
                        "detail": f"{surface_key} is not bound to its agency label",
                    })
                frozen = (
                    agency_marks.get(actual_key)
                    if expected_kind == "agency"
                    else organization_marks.get(actual_key))
                state = attrs.get("data-identity-mark")
                image_sources = [str(image.get("src") or "")
                                 for image in surface.get("images") or []]
                if frozen:
                    if (state != "seal" or frozen not in image_sources
                            or not frozen.startswith("data:image/")):
                        violations.append({
                            "rule": "record_identity_image",
                            "detail": (f"{surface_key} did not render its frozen "
                                       "portable identity mark"),
                        })
                    else:
                        embedded += 1
                elif render_assets is not None:
                    fallback = " ".join(surface.get("fallback") or []).strip()
                    if state != "fallback" or not fallback:
                        violations.append({
                            "rule": "record_identity_fallback",
                            "detail": (f"{surface_key} has no frozen mark and no "
                                       "visible deterministic fallback"),
                        })
                    else:
                        fallbacks += 1
                elif state == "seal" and any(
                        src.startswith("data:image/") for src in image_sources):
                    embedded += 1
                elif (state == "fallback" and
                      " ".join(surface.get("fallback") or []).strip()):
                    fallbacks += 1
                else:
                    violations.append({
                        "rule": "record_identity_fallback",
                        "detail": f"{surface_key} has an empty identity mark",
                    })

    extra = sorted(key for key in by_key if key not in expected_keys)
    if extra:
        violations.append({
            "rule": "unexpected_identity_surface",
            "detail": f"unexpected identity surfaces: {extra[:12]!r}",
        })
    return violations, {
        "expected_placements": expected,
        "priority_reference_placements": priority,
        "owned_or_review_placements": expected - priority,
        "embedded_official_marks": embedded,
        "visible_fallbacks": fallbacks,
    }


def validate_external_product_html(
    client_html: str, doc: ExternalProductDocument, *,
    contract_slots: Any = None, render_assets: Any = None,
) -> dict:
    from agents.golden_press.client_visual_contract import validate_client_visual_contract
    from agents.golden_press.style_contract import unstyled_in_context

    violations = validate_external_product_document(
        doc, contract_slots=contract_slots)
    order = re.findall(r'<section[^>]*data-slot-id="([^"]+)"', client_html)
    expected = [slot.slot_id for slot in doc.slots]
    if order != expected:
        violations.append({"rule": "rendered_slot_order", "detail":
                           f"rendered slots are {order}; expected {expected}"})
    for slot in doc.slots:
        if slot.heading not in client_html:
            violations.append({"rule": "rendered_heading", "detail":
                               f"locked heading is absent: {slot.heading}"})
    violations.extend(_external_html_safety_violations(client_html))
    if "—" in re.sub(r"<[^>]+>", " ", client_html):
        violations.append({"rule": "em_dash", "detail":
                           "an em dash appears in rendered client text"})
    if _LOCAL_PATH.search(client_html):
        violations.append({"rule": "local_path", "detail":
                           "a local filesystem path appears in the client artifact"})
    unstyled = unstyled_in_context(client_html)
    if unstyled:
        violations.append({"rule": "unstyled_classes", "detail":
                           f"unstyled classes: {[name for name, _ in unstyled][:12]}"})
    visual = validate_client_visual_contract(client_html, client_name=doc.client_name)
    violations.extend(visual.get("violations") or [])
    identity_violations, identity_contract = _validate_identity_contract(
        client_html, doc, render_assets=render_assets)
    violations.extend(identity_violations)
    return {
        "schema_version": EXTERNAL_PRODUCT_RENDER_VERSION,
        "ok": not violations,
        "violations": violations,
        "slot_order": order,
        "client_visual_contract": visual,
        "identity_contract": identity_contract,
    }


__all__ = (
    "EXTERNAL_PRODUCT_RENDER_VERSION",
    "capture_external_product_identity_assets",
    "render_external_product",
    "render_slot",
    "validate_external_product_document",
    "validate_external_product_html",
)
