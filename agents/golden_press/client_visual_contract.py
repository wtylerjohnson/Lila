"""Static visual and interaction contract for pressed client HTML.

The market-map data validator answers whether the report says defensible
things.  This module answers a different question: did the *delivered HTML*
retain the marks, links, CSS, and controls that make those claims usable?

The validator is intentionally stdlib-only.  It can run in CI, from a build
receipt, or against a downloaded standalone artifact without starting the
application or fetching the network.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote, urlsplit


CLIENT_VISUAL_CONTRACT_VERSION = "client_visual_contract.v2.2026-08-15"

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_MARK_WORDS = ("logo", "seal", "crest", "emblem", "client-mark",
               "gtm-mark", "brand-mark")
_CLIENT_WORDS = ("client-logo", "client-mark", "clientlogo", "clientmark",
                 "company-logo", "cover-logo")
_ACTION_DATA_KEYS = {
    "data-action", "data-click", "data-command", "data-dialog",
    "data-download", "data-export", "data-href", "data-modal",
    "data-open", "data-print", "data-subscription", "data-studio-action",
    "data-target", "data-url", "data-work", "data-workflow-action",
    "data-filter", "data-filter-scope", "data-close",
}


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    parent: Optional["_Element"] = None
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()

    def ancestors(self) -> Iterable["_Element"]:
        node: Optional[_Element] = self
        while node is not None:
            yield node
            node = node.parent


class _ArtifactParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_Element] = []
        self.elements: list[_Element] = []
        self.images: list[_Element] = []
        self.anchors: list[_Element] = []
        self.buttons: list[_Element] = []
        self.styles: list[_Element] = []
        self.scripts: list[_Element] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.casefold()
        values = {str(key).casefold(): "" if value is None else str(value)
                  for key, value in attrs}
        element = _Element(tag=tag, attrs=values,
                           parent=self.stack[-1] if self.stack else None)
        self.elements.append(element)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "img":
            self.images.append(element)
        elif tag == "a":
            self.anchors.append(element)
        elif tag == "button":
            self.buttons.append(element)
        elif tag == "style":
            self.styles.append(element)
        elif tag == "script":
            self.scripts.append(element)
        if tag not in _VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        # Append to every open ancestor so a button's label includes nested
        # spans and a style/script element retains its full source.
        for element in self.stack:
            element.text_parts.append(data)


def _semantic_blob(element: _Element) -> str:
    values: list[str] = []
    for node in element.ancestors():
        values.extend((node.attrs.get("id", ""), node.attrs.get("class", ""),
                       node.attrs.get("alt", ""), node.attrs.get("title", ""),
                       node.attrs.get("data-logo-id", ""),
                       node.attrs.get("data-logo-slot-id", ""),
                       node.attrs.get("data-mark", "")))
    return " ".join(values).casefold()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _repeated_table_sentences(elements: list[_Element]) -> list[tuple[str, int]]:
    """Find long explanatory sentences repeated across many table cells.

    Repeated labels and route names are normal. Repeating the same eight-word
    analysis sentence across eight or more rows is a generator-field defect:
    classification prose has been substituted for record-specific evidence.
    """
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for element in elements:
        if element.tag not in {"td", "th"}:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", element.text):
            normalized = re.sub(r"\s+", " ", sentence).strip()
            key = _slug(normalized)
            if len(key.split()) < 8:
                continue
            counts[key] += 1
            display.setdefault(key, normalized)
    return sorted(
        ((display[key], count) for key, count in counts.items() if count >= 8),
        key=lambda item: (-item[1], item[0]),
    )


def _data_image_dimensions(src: str) -> Optional[tuple[int, int]]:
    """Return embedded raster/SVG dimensions when cheaply recoverable."""
    if not src.casefold().startswith("data:image/") or "," not in src:
        return None
    header, payload = src.split(",", 1)
    try:
        if ";base64" in header.casefold():
            raw = base64.b64decode(payload, validate=False)
        else:
            raw = unquote(payload).encode("utf-8", errors="ignore")
    except (ValueError, TypeError):
        return None

    if len(raw) >= 10 and raw[:6] in (b"GIF87a", b"GIF89a"):
        return (int.from_bytes(raw[6:8], "little"),
                int.from_bytes(raw[8:10], "little"))
    if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n":
        return (int.from_bytes(raw[16:20], "big"),
                int.from_bytes(raw[20:24], "big"))
    if "svg" in header.casefold():
        source = raw.decode("utf-8", errors="ignore")
        width = re.search(r"\bwidth\s*=\s*['\"]?([0-9.]+)", source, re.I)
        height = re.search(r"\bheight\s*=\s*['\"]?([0-9.]+)", source, re.I)
        if width and height:
            return int(float(width.group(1))), int(float(height.group(1)))
        viewbox = re.search(
            r"\bviewBox\s*=\s*['\"]\s*[-0-9.]+\s+[-0-9.]+\s+"
            r"([0-9.]+)\s+([0-9.]+)", source, re.I)
        if viewbox:
            return int(float(viewbox.group(1))), int(float(viewbox.group(2)))
    return None


def _is_mark_candidate(image: _Element) -> bool:
    blob = _semantic_blob(image)
    # `market-map` contains the letters "mark" but is not itself a brand
    # marker.  Only accept mark as a complete class/id token.
    return (any(word in blob for word in _MARK_WORDS) or
            re.search(r"(?<![a-z0-9])mark(?![a-z0-9])", blob) is not None)


def _is_usable_image(image: _Element) -> bool:
    src = image.attrs.get("src", "").strip()
    if not src:
        return False
    return _data_image_dimensions(src) != (1, 1)


def _has_inline_svg(elements: list[_Element], words: tuple[str, ...]) -> bool:
    return any(element.tag == "svg" and any(word in _semantic_blob(element)
                                             for word in words)
               for element in elements)


def _is_placeholder_action(value: str) -> bool:
    clean = str(value or "").strip().casefold()
    if clean in {"", "#"}:
        return True
    return clean.startswith("javascript:") or clean in {
        "void(0)", "javascript", "about:blank",
    }


def _css_brace_error(css: str) -> Optional[str]:
    """Return a concise error while ignoring comments and quoted strings."""
    balance = 0
    quote: Optional[str] = None
    escaped = False
    in_comment = False
    index = 0
    while index < len(css):
        char = css[index]
        nxt = css[index + 1] if index + 1 < len(css) else ""
        if in_comment:
            if char == "*" and nxt == "/":
                in_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and nxt == "*":
            in_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
            if balance < 0:
                return f"closing brace at character {index} has no opener"
        index += 1
    if balance:
        return f"{balance} opening brace(s) are not closed"
    return None


def _meaningful_onclick(value: str) -> bool:
    clean = re.sub(r"\s+", "", str(value or "")).casefold()
    return bool(clean) and clean not in {
        "returnfalse", "returnfalse;", "void(0)", "void(0);",
        "javascript:void(0)", "javascript:void(0);",
    }


def _runtime_handles_button(button: _Element, script_text: str) -> bool:
    if not script_text.strip():
        return False
    low = script_text.casefold()
    if not any(token in low for token in (
        "addeventlistener", ".onclick", "onclick=", "click(",
    )):
        return False

    attrs = button.attrs
    identity_tokens = [attrs.get("id", "")]
    identity_tokens.extend(attrs.get("class", "").split())
    identity_tokens.extend(attrs.get(key, "") for key in _ACTION_DATA_KEYS)
    if any(token and token.casefold() in low for token in identity_tokens):
        return True
    return any(key in attrs and key in low for key in _ACTION_DATA_KEYS)


def _button_has_action(button: _Element, parser: _ArtifactParser) -> bool:
    attrs = button.attrs
    if "disabled" in attrs or attrs.get("aria-disabled", "").casefold() == "true":
        return False
    if _meaningful_onclick(attrs.get("onclick", "")):
        return True
    for key in ("href", "formaction"):
        if attrs.get(key) and not _is_placeholder_action(attrs[key]):
            return True

    for node in button.ancestors():
        if node is not button and node.tag == "a":
            href = node.attrs.get("href", "")
            if href and not _is_placeholder_action(href):
                return True

    target = attrs.get("popovertarget", "") or attrs.get("commandfor", "")
    if target and target in parser.ids:
        return True
    if attrs.get("type", "").casefold() == "reset":
        return any(node.tag == "form" for node in button.ancestors())

    if attrs.get("type", "submit").casefold() == "submit":
        form_id = attrs.get("form", "")
        forms = [node for node in parser.elements if node.tag == "form" and
                 (not form_id or node.attrs.get("id") == form_id)]
        if not form_id:
            forms = [node for node in button.ancestors() if node.tag == "form"]
        for form in forms:
            action = form.attrs.get("action", "")
            if action and not _is_placeholder_action(action):
                return True

    script_text = "\n".join(script.text for script in parser.scripts)
    return _runtime_handles_button(button, script_text)


def validate_client_visual_contract(
    html: str, *, client_name: str = ""
) -> dict:
    """Validate one final client artifact without fetching the network."""
    parser = _ArtifactParser()
    parser.feed(html or "")
    parser.close()
    violations: list[dict[str, str]] = []

    mark_images = [image for image in parser.images if _is_mark_candidate(image)]
    one_pixel_marks = [image for image in mark_images
                       if _data_image_dimensions(image.attrs.get("src", "")) == (1, 1)]
    if one_pixel_marks:
        labels = [image.attrs.get("alt") or image.attrs.get("id") or "unlabeled mark"
                  for image in one_pixel_marks]
        sample = ", ".join(labels[:5])
        if len(labels) > 5:
            sample += f", +{len(labels) - 5} more"
        violations.append({
            "rule": "transparent_1x1_mark",
            "detail": f"{len(one_pixel_marks)} logo/seal image(s) resolve to a "
                      f"1x1 placeholder: {sample}",
        })

    client_norm = _slug(client_name)
    client_words = _CLIENT_WORDS + ((client_norm,) if client_norm else ())
    client_images = [image for image in parser.images
                     if any(word and word in _semantic_blob(image)
                            for word in client_words)]
    client_mark_ok = any(_is_usable_image(image) for image in client_images)
    client_mark_ok = client_mark_ok or _has_inline_svg(parser.elements, client_words)
    if not client_mark_ok:
        label = client_name.strip() or "client"
        violations.append({
            "rule": "missing_client_mark",
            "detail": f"no usable {label} logo/mark is present in the client artifact",
        })

    gtm_words = ("gtm", "go-to-market", "go to market")
    gtm_images = [image for image in parser.images
                  if any(word in _semantic_blob(image) for word in gtm_words)]
    gtm_mark_ok = any(_is_usable_image(image) for image in gtm_images)
    gtm_mark_ok = gtm_mark_ok or _has_inline_svg(parser.elements, gtm_words)
    if not gtm_mark_ok:
        violations.append({
            "rule": "missing_gtm_mark",
            "detail": "no usable GTM logo/mark is present in the client artifact",
        })

    malformed_awards = sorted({anchor.attrs.get("href", "")
                               for anchor in parser.anchors
                               if "usaspending.gov/award/" in
                               anchor.attrs.get("href", "").casefold() and
                               "_-none-_-none-" in
                               anchor.attrs.get("href", "").casefold()})
    if malformed_awards:
        violations.append({
            "rule": "malformed_usaspending_award_route",
            "detail": f"{len(malformed_awards)} USAspending award route(s) contain "
                      "the unresolved _-NONE-_-NONE- suffix",
        })

    placeholder_links = [anchor for anchor in parser.anchors
                         if "href" in anchor.attrs and
                         _is_placeholder_action(anchor.attrs["href"])]
    if placeholder_links:
        labels = [anchor.text or anchor.attrs.get("aria-label") or "unlabeled link"
                  for anchor in placeholder_links]
        violations.append({
            "rule": "placeholder_action_href",
            "detail": f"{len(placeholder_links)} anchor(s) use #, an empty href, or "
                      f"javascript: instead of a working destination: "
                      f"{', '.join(labels[:5])}",
        })

    id_counts = Counter(parser.ids)
    duplicate_ids = sorted(value for value, count in id_counts.items() if count > 1)
    if duplicate_ids:
        violations.append({
            "rule": "duplicate_id",
            "detail": "duplicate HTML id(s): " + ", ".join(duplicate_ids),
        })

    missing_targets: list[str] = []
    for anchor in parser.anchors:
        href = anchor.attrs.get("href", "").strip()
        if not href.startswith("#") or href == "#":
            continue
        fragment = unquote(urlsplit(href).fragment)
        if fragment and fragment not in id_counts:
            missing_targets.append(fragment)
    if missing_targets:
        violations.append({
            "rule": "missing_internal_target",
            "detail": "internal fragment target(s) absent: " +
                      ", ".join(sorted(set(missing_targets))),
        })

    repeated_table_prose = _repeated_table_sentences(parser.elements)
    if repeated_table_prose:
        sample = "; ".join(
            f'{count}x "{sentence[:160]}"'
            for sentence, count in repeated_table_prose[:3]
        )
        violations.append({
            "rule": "repeated_table_prose",
            "detail": "long explanatory prose is repeated across table rows; "
                      "render record-specific evidence or a published route "
                      f"instead: {sample}",
        })

    css_errors: list[str] = []
    for index, style in enumerate(parser.styles, start=1):
        error = _css_brace_error(style.text)
        if error:
            css_errors.append(f"style block {index}: {error}")
    if css_errors:
        violations.append({
            "rule": "unbalanced_css_braces",
            "detail": "; ".join(css_errors),
        })

    inert_buttons = [button for button in parser.buttons
                     if not _button_has_action(button, parser)]
    if inert_buttons:
        labels = [button.text or button.attrs.get("aria-label") or
                  button.attrs.get("id") or "unlabeled button"
                  for button in inert_buttons]
        violations.append({
            "rule": "button_without_action",
            "detail": f"{len(inert_buttons)} button(s) have no working href, form "
                      f"action, inline handler, or bound runtime: "
                      f"{', '.join(labels[:8])}",
        })

    return {
        "version": CLIENT_VISUAL_CONTRACT_VERSION,
        "ok": not violations,
        "violations": violations,
        "receipts": {
            "images": len(parser.images),
            "mark_images": len(mark_images),
            "one_pixel_marks": len(one_pixel_marks),
            "anchors": len(parser.anchors),
            "internal_anchors": sum(
                1 for anchor in parser.anchors
                if anchor.attrs.get("href", "").startswith("#")),
            "buttons": len(parser.buttons),
            "ids": len(parser.ids),
            "style_blocks": len(parser.styles),
            "script_blocks": len(parser.scripts),
        },
    }


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate marks, links, CSS, and controls in client HTML")
    parser.add_argument("html", type=Path, help="pressed client HTML artifact")
    parser.add_argument("--client", default="", help="expected client name")
    args = parser.parse_args(argv)
    try:
        source = args.html.read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    result = validate_client_visual_contract(source, client_name=args.client)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main())
