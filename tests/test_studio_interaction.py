"""Interaction contract for the consolidated single-runtime studio.

CONVENTIONS law (2026-08-04): no interactive feature is claimed working
without an automated interaction test. This is that test for the skeleton
editor's native mark handling. It presses a representative pack through the
REAL press seam, then in a real browser: drops a fixture PNG onto a band
mark tile and onto the GTM header slot, saves through the layout's own
SAVE HTML path, reloads the saved bytes, and requires both images to
survive byte-faithfully (the setLogoValue draft-restore regression class).
It also exercises the double-click file picker and Alt-click
clear-to-monogram.

Set LILA_STUDIO_ARTIFACT to a pressed studio artifact on disk to run the
same proof against a real press (the delivery proof). Set LILA_STUDIO_SHOTS
to a directory to keep the four before/after screenshots.
"""
from __future__ import annotations

import base64
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from tests.test_markup_coverage import _pack  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

def _fixture_png() -> bytes:
    """A deterministic 64x64 solid-red PNG, visible in proof screenshots.

    Built in-process so the exact bytes (and therefore the exact data URL
    that must survive the save/reload round trip) are pinned by code."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return (struct.pack(">I", len(data)) + payload
                + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF))

    size = 64
    row = b"\x00" + b"\xc8\x1e\x2d\xff" * size
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size, 9))
            + chunk(b"IEND", b""))


FIXTURE_PNG = _fixture_png()
FIXTURE_DATA_URL = ("data:image/png;base64,"
                    + base64.b64encode(FIXTURE_PNG).decode("ascii"))

DROP_JS = """([sel, dataUrl]) => {
  const el = document.querySelector(sel);
  if (!el) throw new Error("drop target not found: " + sel);
  const b64 = dataUrl.split(",")[1];
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], "fixture.png", { type: "image/png" }));
  for (const type of ["dragover", "drop"]) {
    el.dispatchEvent(new DragEvent(type, {
      bubbles: true, cancelable: true, dataTransfer: dt }));
  }
}"""


def _chrome():
    from tools.export_pdf import find_chrome

    try:
        return find_chrome()
    except Exception:
        return None


def _studio_document() -> str:
    override = os.environ.get("LILA_STUDIO_ARTIFACT")
    if override:
        document = Path(override).read_text(encoding="utf-8")
        if "lila-studio-runtime" in document:
            pytest.skip("LILA_STUDIO_ARTIFACT predates the single-runtime "
                        "consolidation; re-press before running the proof")
        return document
    from agents.golden_press.press import _render_saved_inputs

    _content, spliced, _verdict, receipt = _render_saved_inputs(
        "Acme Networks", slug="acme-networks", root=ROOT, pack=_pack(),
        prose={})
    assert receipt["runtime"] == "layout-native"
    return spliced


def _shot(page, locator, path: Path) -> None:
    """Screenshot the element in context: its nearest row/card/header
    ancestor, so the proof shows a readable surface, not clip math."""
    context = locator.locator(
        "xpath=ancestor-or-self::*[self::tr or self::article "
        "or self::header or self::section][1]")
    target = context if context.count() else locator
    target.scroll_into_view_if_needed()
    target.screenshot(path=str(path))


def test_native_mark_handling_survives_save_and_reload(tmp_path):
    chrome = _chrome()
    if not chrome:
        pytest.skip("no system Chrome available for the interaction test")

    shots = Path(os.environ.get("LILA_STUDIO_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)

    document = _studio_document()
    # ONE runtime, full stop: the skeleton's own editor script and nothing
    # else. The two injected systems must be gone from the press output.
    assert document.count("<script") == 1, (
        "press output must carry exactly the skeleton editor script")
    assert "lila-studio-runtime" not in document
    assert "lila-studio-state" not in document

    artifact = tmp_path / "studio.html"
    artifact.write_text(document, encoding="utf-8")
    fixture_png = tmp_path / "fixture.png"
    fixture_png.write_bytes(FIXTURE_PNG)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
                                             executable_path=chrome)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(artifact.as_uri(), wait_until="load")

        # (a) a band mark tile that renders the monogram, (b) the GTM slot.
        tile = page.locator(".pmk.no-img, .seal.no-img, .mk.no-img").first
        tile.wait_for(state="attached")
        tile_key = tile.get_attribute("data-logo-slot-id")
        assert tile_key, "editor must assign a stable key to every mark tile"
        tile_sel = f'[data-logo-slot-id="{tile_key}"]'
        monogram = (tile.inner_text() or "").strip()
        # Rebind to the stable key: the drop flips no-img -> has-img, which
        # must un-match the class-based discovery locator.
        tile = page.locator(tile_sel)
        assert monogram, "the tile under test must start as a text monogram"
        assert tile.locator("img").count() == 0

        gtm = page.locator('img[data-logo-id*="gtm"]').first
        gtm.wait_for(state="attached")
        gtm_key = gtm.get_attribute("data-logo-id")
        gtm_sel = f'img[data-logo-id="{gtm_key}"]'
        gtm_original_src = gtm.get_attribute("src")

        _shot(page, tile, shots / "mark-tile-before.png")
        _shot(page, gtm, shots / "gtm-slot-before.png")

        page.locator('[data-action="toggle-edit"]').click()

        page.evaluate(DROP_JS, [tile_sel, FIXTURE_DATA_URL])
        page.wait_for_function(
            """(sel) => {
              const img = document.querySelector(sel + " img");
              return img && img.src.startsWith("data:image/png;base64,");
            }""", arg=tile_sel)
        assert (tile.inner_text() or "").strip() == "", (
            "monogram text must be gone once an image lands")
        assert tile.locator("img").get_attribute("src") == FIXTURE_DATA_URL
        assert "has-img" in (tile.get_attribute("class") or "")

        page.evaluate(DROP_JS, [gtm_sel, FIXTURE_DATA_URL])
        page.wait_for_function(
            """(sel) => document.querySelector(sel)
                 .src.startsWith("data:image/png;base64,")""", arg=gtm_sel)
        assert gtm.get_attribute("src") == FIXTURE_DATA_URL
        assert gtm.get_attribute("src") != gtm_original_src

        # Save through the layout's own SAVE HTML path.
        with page.expect_download() as download_info:
            page.locator('[data-action="download-html"]').click()
        saved = tmp_path / "saved.html"
        download_info.value.save_as(saved)

        saved_bytes = saved.read_text(encoding="utf-8")
        assert saved_bytes.count(FIXTURE_DATA_URL) == 2, (
            "both dropped images must persist byte-faithfully in the saved "
            "artifact")
        assert saved_bytes.count("<script") == 1
        # The transient picker element never serializes (the attribute name
        # legitimately appears once inside the editor's own script source).
        assert not re.search(r"<input[^>]*data-editor-file-input",
                             saved_bytes)

        # Reload the saved bytes; the draft-restore path (setLogoValue) runs
        # over them and must not corrupt either image.
        page.goto(saved.as_uri(), wait_until="load")
        tile = page.locator(tile_sel)
        gtm = page.locator(gtm_sel)
        assert tile.locator("img").get_attribute("src") == FIXTURE_DATA_URL
        assert (tile.inner_text() or "").strip() == ""
        assert gtm.get_attribute("src") == FIXTURE_DATA_URL

        _shot(page, tile, shots / "mark-tile-after-save-reload.png")
        _shot(page, gtm, shots / "gtm-slot-after-save-reload.png")
        # The proof must SHOW the change: a framing bug that screenshots the
        # same pixels twice is a test failure, not a delivered proof.
        for name in ("mark-tile", "gtm-slot"):
            before = (shots / f"{name}-before.png").read_bytes()
            after = (shots / f"{name}-after-save-reload.png").read_bytes()
            assert before != after, (
                f"{name} before/after screenshots are identical; the proof "
                "shows no visual change")

        # Alt-click clears the tile back to a monogram.
        page.locator('[data-action="toggle-edit"]').click()
        tile.click(modifiers=["Alt"])
        page.wait_for_function(
            """(sel) => {
              const el = document.querySelector(sel);
              return !el.querySelector("img") && el.textContent.trim();
            }""", arg=tile_sel)
        assert "no-img" in (tile.get_attribute("class") or "")

        # Double-click opens the file picker; picking a file lands the image.
        tile.dblclick()
        page.set_input_files("[data-editor-file-input]", fixture_png)
        page.wait_for_function(
            """(sel) => {
              const img = document.querySelector(sel + " img");
              return img && img.src.startsWith("data:image/png;base64,");
            }""", arg=tile_sel)
        assert tile.locator("img").get_attribute("src") == FIXTURE_DATA_URL

        assert errors == [], f"the editor raised page errors: {errors}"
        context.close()
        browser.close()
