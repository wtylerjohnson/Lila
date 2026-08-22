"""Compile a pressed LILA report into a self-contained Editable Studio.

This module is deliberately the *last* stage of the golden press. Retrieval,
selection, arithmetic, source URLs, seals, and prose all finish before this
compiler runs. The compiler adds authoring capability; it does not rewrite the
report's evidence or ask a model to touch HTML.

The contract is fail-closed:

* every existing href and image source survives byte-for-byte;
* every ``data-edit-id`` and logo/seal slot survives exactly once;
* every evidence row survives;
* the Studio runtime and stylesheet are embedded exactly once; and
* the output carries a receipt proving those invariants.

The browser runtime then supplies section reordering/removal/recovery, custom
sections and text/callout/link/logo blocks, hyperlink editing, text and image
resizing, undo/redo, browser draft persistence, and downloadable/reopenable
HTML. It builds on the small text/logo editor already carried by the golden
skeleton instead of replacing it.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional


STUDIO_SCHEMA_VERSION = "golden_press.editable_studio.v1"
STUDIO_RUNTIME_VERSION = 2
STYLE_ID = "lila-studio-style"
STATE_ID = "lila-studio-state"
RUNTIME_ID = "lila-studio-runtime"
_ASSET_DIR = Path(__file__).with_name("assets")
_ATTR_RE_TEMPLATE = r"(?P<prefix>\s){name}\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)"


class StudioCompileError(RuntimeError):
    """The source is not a valid Studio input, or preservation failed."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "report"


def _filename_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_") or "Federal_Report"


def _counter_payload(values: Counter[str]) -> list[list[object]]:
    return [[key, values[key]] for key in sorted(values)]


class _DocumentInventory(HTMLParser):
    """Count only real DOM start tags, never markup-looking text in scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.hrefs: Counter[str] = Counter()
        self.image_sources: Counter[str] = Counter()
        self.edit_ids: Counter[str] = Counter()
        self.logo_ids: Counter[str] = Counter()
        self.ids: Counter[str] = Counter()
        self.evidence_rows = 0
        self.sections = 0
        self.report_tools = 0
        self.signal_boards = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        node_id = values.get("id")
        if node_id:
            self.ids[node_id] += 1
            if node_id == "signal-board":
                self.signal_boards += 1
        if tag == "a" and "href" in values:
            self.hrefs[values["href"]] += 1
        if tag == "img" and "src" in values:
            self.image_sources[values["src"]] += 1
        if values.get("data-edit-id"):
            self.edit_ids[values["data-edit-id"]] += 1
        logo_id = values.get("data-logo-slot-id") or values.get("data-logo-id")
        if logo_id:
            self.logo_ids[logo_id] += 1
        if "sb-evidence-row" in classes:
            self.evidence_rows += 1
        if "report-tools" in classes or "data-editor-ui" in values:
            self.report_tools += 1
        if tag == "section":
            self.sections += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)

    def payload(self) -> dict:
        return {
            "hrefs": _counter_payload(self.hrefs),
            "image_sources": _counter_payload(self.image_sources),
            "edit_ids": _counter_payload(self.edit_ids),
            "logo_ids": _counter_payload(self.logo_ids),
            "evidence_rows": self.evidence_rows,
            "sections": self.sections,
        }


def inventory(document: str) -> _DocumentInventory:
    result = _DocumentInventory()
    try:
        result.feed(document)
        result.close()
    except Exception as exc:  # pragma: no cover - HTMLParser rarely raises
        raise StudioCompileError(f"source HTML could not be inventoried: {exc}") from exc
    return result


def _duplicate_keys(values: Counter[str]) -> list[str]:
    return sorted(key for key, count in values.items() if count != 1)


def _require_source_contract(document: str, source: _DocumentInventory) -> None:
    if document.count(f'id="{STYLE_ID}"') or document.count(f'id="{RUNTIME_ID}"'):
        raise StudioCompileError("source already contains the Editable Studio runtime")
    if "data-studio-integrity" in document:
        raise StudioCompileError(
            "source already contains a compiled Studio root stamp")
    if source.signal_boards != 1:
        raise StudioCompileError(
            f"source must contain exactly one #signal-board; found {source.signal_boards}")
    if source.report_tools < 1 or "data-editor-ui" not in document:
        raise StudioCompileError("source is missing the inherited report editor chrome")
    for action in ("toggle-edit", "save-draft", "download-html"):
        if not re.search(rf"data-action\s*=\s*['\"]{re.escape(action)}['\"]", document):
            raise StudioCompileError(f"source editor is missing its {action!r} action")
    if not source.edit_ids:
        raise StudioCompileError("source contains no editable fields")
    duplicate_edits = _duplicate_keys(source.edit_ids)
    duplicate_logos = _duplicate_keys(source.logo_ids)
    if duplicate_edits:
        raise StudioCompileError(
            "source contains duplicate editable field ids: " + ", ".join(duplicate_edits[:8]))
    if duplicate_logos:
        raise StudioCompileError(
            "source contains duplicate logo/seal slot ids: " + ", ".join(duplicate_logos[:8]))
    if len(re.findall(r"</head\s*>", document, flags=re.I)) != 1:
        raise StudioCompileError("source must contain exactly one closing head tag")
    if len(re.findall(r"</body\s*>", document, flags=re.I)) != 1:
        raise StudioCompileError("source must contain exactly one closing body tag")


def _html_tag(document: str) -> tuple[re.Match[str], str]:
    match = re.search(r"<html\b[^>]*>", document, flags=re.I)
    if not match:
        raise StudioCompileError("source has no html root tag")
    return match, match.group(0)


def _get_attr(tag: str, name: str) -> str:
    pattern = re.compile(_ATTR_RE_TEMPLATE.format(name=re.escape(name)), re.I | re.S)
    match = pattern.search(tag)
    return html_lib.unescape(match.group("value")) if match else ""


def _set_attr(tag: str, name: str, value: str) -> str:
    encoded = html_lib.escape(value, quote=True)
    pattern = re.compile(_ATTR_RE_TEMPLATE.format(name=re.escape(name)), re.I | re.S)
    if pattern.search(tag):
        return pattern.sub(lambda m: f'{m.group("prefix")}{name}="{encoded}"', tag, count=1)
    return tag[:-1] + f' {name}="{encoded}">'


def _source_title(document: str) -> str:
    match = re.search(r"<title>(.*?)</title\s*>", document, flags=re.I | re.S)
    if not match:
        return "Federal Opportunity Pre-Assessment"
    return html_lib.unescape(re.sub(r"\s+", " ", match.group(1)).strip())


def _replace_title(document: str, title: str) -> str:
    encoded = html_lib.escape(title, quote=False)
    pattern = re.compile(r"<title>.*?</title\s*>", flags=re.I | re.S)
    if pattern.search(document):
        return pattern.sub(f"<title>{encoded}</title>", document, count=1)
    return re.sub(r"</head\s*>", f"  <title>{encoded}</title>\n</head>", document,
                  count=1, flags=re.I)


def _default_download_name(current: str, client_name: str, stamp: str) -> str:
    if current:
        base = re.sub(r"\.html?$", "", current, flags=re.I)
        base = re.sub(r"(?:_EDITABLE)?(?:_STUDIO)?$", "", base, flags=re.I)
        return f"{base}_EDITABLE_STUDIO.html"
    dated = f"_{stamp}" if stamp else ""
    return f"{_filename_stem(client_name)}_Federal_Opportunity_Pre_Assessment_EDITABLE_STUDIO{dated}.html"


def _load_asset(name: str) -> str:
    path = _ASSET_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StudioCompileError(f"Studio asset is unavailable: {path}") from exc


def _assert_preserved(before: _DocumentInventory, after: _DocumentInventory,
                      *, expect_markers: bool = True) -> None:
    comparisons: Iterable[tuple[str, object, object]] = (
        ("hrefs", before.hrefs, after.hrefs),
        ("image sources", before.image_sources, after.image_sources),
        ("editable field ids", before.edit_ids, after.edit_ids),
        ("logo/seal slot ids", before.logo_ids, after.logo_ids),
        ("evidence rows", before.evidence_rows, after.evidence_rows),
    )
    changed = [label for label, old, new in comparisons if old != new]
    if changed:
        raise StudioCompileError(
            "Editable Studio compilation changed source content: " + ", ".join(changed))
    expected = 1 if expect_markers else 0
    for marker in (STYLE_ID, STATE_ID, RUNTIME_ID):
        if after.ids.get(marker, 0) != expected:
            raise StudioCompileError(
                f"compiled output must contain exactly {expected} #{marker}; "
                f"found {after.ids.get(marker, 0)}")


@dataclass(frozen=True)
class StudioBuild:
    html: str
    receipt: dict


def compile_editable_studio(
    document: str,
    *,
    client_name: str,
    stamp: str = "",
    report_id: Optional[str] = None,
    download_name: Optional[str] = None,
    title: Optional[str] = None,
    inject_runtime: bool = True,
) -> StudioBuild:
    """Return a Studio HTML document and its preservation receipt.

    With ``inject_runtime=False`` the document keeps exactly ONE editing
    system: the golden skeleton's own inherited editor. No studio style,
    state, or runtime script is added; the receipt is still computed and
    records ``runtime: layout-native``. This is the press path (2026-08-04
    consolidation: the three-runtime artifact broke logo drop)."""

    before = inventory(document)
    _require_source_contract(document, before)
    source_sha256 = _sha256(document)

    html_match, root_tag = _html_tag(document)
    current_report_id = _get_attr(root_tag, "data-report-id")
    current_download_name = _get_attr(root_tag, "data-download-name")
    final_report_id = report_id or (
        f"{_slug(client_name)}-federal-opportunity-editable-studio-{stamp}"
        if stamp else f"{_slug(current_report_id or client_name)}-editable-studio")
    final_download_name = download_name or _default_download_name(
        current_download_name, client_name, stamp)
    source_title = _source_title(document)
    final_title = title or (
        source_title if source_title.casefold().endswith("editable studio")
        else f"{source_title} · Editable Studio")

    new_root = root_tag
    for name, value in (
        ("data-report-id", final_report_id),
        ("data-download-name", final_download_name),
        ("data-studio-client", client_name),
        ("data-studio-schema", STUDIO_SCHEMA_VERSION),
        ("data-studio-source-sha256", source_sha256),
        ("data-studio-integrity", "pristine"),
    ):
        new_root = _set_attr(new_root, name, value)
    output = document[:html_match.start()] + new_root + document[html_match.end():]
    output = _replace_title(output, final_title)

    if inject_runtime:
        style = _load_asset("editable_studio.css")
        runtime = _load_asset("editable_studio.js")
        output = re.sub(
            r"</head\s*>",
            lambda _m: f'  <style id="{STYLE_ID}">\n{style}\n  </style>\n</head>',
            output, count=1, flags=re.I)
        output = re.sub(
            r"</body\s*>",
            lambda _m: (
                f'  <script type="application/json" id="{STATE_ID}"></script>\n'
                f'  <script id="{RUNTIME_ID}">\n{runtime}\n  </script>\n</body>'),
            output, count=1, flags=re.I)

    after = inventory(output)
    _assert_preserved(before, after, expect_markers=inject_runtime)
    receipt = {
        "schema_version": STUDIO_SCHEMA_VERSION,
        "runtime_version": STUDIO_RUNTIME_VERSION,
        "runtime": "injected" if inject_runtime else "layout-native",
        "client_name": client_name,
        "report_id": final_report_id,
        "download_name": final_download_name,
        "title": final_title,
        "source_sha256": source_sha256,
        "output_sha256": _sha256(output),
        "source": before.payload(),
        "output": after.payload(),
        "preserved": {
            "hrefs": before.hrefs == after.hrefs,
            "image_sources": before.image_sources == after.image_sources,
            "editable_field_ids": before.edit_ids == after.edit_ids,
            "logo_seal_slot_ids": before.logo_ids == after.logo_ids,
            "evidence_rows": before.evidence_rows == after.evidence_rows,
        },
        "studio_markers": {
            marker: after.ids.get(marker, 0)
            for marker in (STYLE_ID, STATE_ID, RUNTIME_ID)
        },
        "ready": True,
    }
    return StudioBuild(html=output, receipt=receipt)


def compile_file(
    source_path: Path,
    output_path: Path,
    *,
    client_name: str,
    stamp: str = "",
    receipt_path: Optional[Path] = None,
    report_id: Optional[str] = None,
    download_name: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Compile one file atomically and optionally write its JSON receipt."""

    source = source_path.read_text(encoding="utf-8")
    build = compile_editable_studio(
        source,
        client_name=client_name,
        stamp=stamp,
        report_id=report_id,
        download_name=download_name,
        title=title,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(output_path.name + ".tmp")
    temp.write_text(build.html, encoding="utf-8")
    os.replace(temp, output_path)
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temp_receipt = receipt_path.with_name(receipt_path.name + ".tmp")
        temp_receipt.write_text(
            json.dumps(build.receipt, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        os.replace(temp_receipt, receipt_path)
    return build.receipt


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile an LILA golden report into an Editable Studio")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--client", required=True)
    parser.add_argument("--stamp", default="")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    receipt = compile_file(
        args.source, args.output, client_name=args.client, stamp=args.stamp,
        receipt_path=args.receipt)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by operators
    raise SystemExit(main())
