"""Real-browser contract for the generated Editable Studio.

The source-string tests prove that compilation preserves the pressed report.
This test proves the other half of the contract: Chrome can edit, download,
and reopen the self-contained artifact without replaying a model or losing the
evidence-bearing DOM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from agents.golden_press.studio import compile_file


ROOT = Path(__file__).resolve().parents[1]


def _chrome():
    from tools.export_pdf import find_chrome

    try:
        return find_chrome()
    except Exception:
        return None


def _surfaces(page) -> dict:
    return page.evaluate(
        """() => {
          const root = document.querySelector('#signal-board');
          return {
            hrefs: [...root.querySelectorAll('a[href]')].map((n) => n.getAttribute('href')),
            images: [...root.querySelectorAll('img[src]')].map((n) => n.getAttribute('src')),
            edits: [...root.querySelectorAll('[data-edit-id]')].map((n) => n.dataset.editId),
            logos: [...root.querySelectorAll('[data-logo-slot-id],img[data-logo-id]')]
              .map((n) => n.dataset.logoSlotId || n.dataset.logoId),
            evidence: root.querySelectorAll('.sb-evidence-row').length,
          };
        }"""
    )


def _new_page(browser):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, errors


def test_studio_edit_download_and_clean_reopen(tmp_path):
    chrome = _chrome()
    if not chrome:
        pytest.skip("no system Chrome available for Editable Studio test")

    compiled = tmp_path / "riverbed.studio.html"
    compile_file(
        ROOT / "fixtures/golden/riverbed_golden.html",
        compiled,
        client_name="Riverbed",
        stamp="2026-07-30",
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=chrome)

        source_context, source_page, source_errors = _new_page(browser)
        source_page.goto(compiled.as_uri(), wait_until="load")
        source_page.wait_for_selector("html[data-studio-boot='ready']")
        original = _surfaces(source_page)
        assert source_errors == []
        assert source_page.get_attribute("html", "data-studio-integrity") == "pristine"
        assert source_page.locator("[data-action='toggle-studio']").count() == 1

        # An untouched download is still the same certified evidence surface.
        with source_page.expect_download() as pristine_info:
            source_page.locator("[data-action='download-html']").click()
        pristine_download = tmp_path / "pristine-reopen.html"
        pristine_info.value.save_as(pristine_download)
        source_context.close()

        pristine_context, page, pristine_errors = _new_page(browser)
        page.goto(pristine_download.as_uri(), wait_until="load")
        page.wait_for_selector("html[data-studio-boot='ready']")
        assert pristine_errors == []
        assert _surfaces(page) == original
        assert page.get_attribute("html", "data-studio-integrity") == "pristine"

        # Exercise the authoring layer: add a section, add text and a logo
        # box, remove a source section, then download the edited HTML.
        page.locator("[data-action='toggle-studio']").click()
        page.locator("[data-studio-action='add-section']").click()
        page.locator("[data-studio-add='text']").click()
        text_field = page.locator(".studio-user-block [data-edit-id]").last
        text_field.evaluate(
            """node => {
              node.innerHTML = 'Account-team hypothesis to validate';
              node.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
            }"""
        )
        page.locator("[data-studio-add='image']").click()
        image_file = tmp_path / "test-seal.svg"
        image_file.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
            '<circle cx="20" cy="20" r="18" fill="#0b1b31"/></svg>',
            encoding="utf-8",
        )
        page.locator("[data-studio-image-file]").set_input_files(image_file)
        page.wait_for_function(
            """() => [...document.querySelectorAll('.studio-user-block img')]
              .some((img) => img.src.startsWith('data:image/svg+xml'))"""
        )
        forecast_row = page.locator("[data-studio-section-ref='forecast']")
        forecast_row.locator("[data-studio-row-action='hide']").click()
        assert page.get_attribute("html", "data-studio-integrity") == "analyst-edited-unverified"

        with page.expect_download() as edited_info:
            page.locator("[data-studio-action='download']").click()
        edited_download = tmp_path / "edited-reopen.html"
        edited_info.value.save_as(edited_download)
        pristine_context.close()

        edited_context, reopened, reopened_errors = _new_page(browser)
        reopened.goto(edited_download.as_uri(), wait_until="load")
        reopened.wait_for_selector("html[data-studio-boot='ready']")
        assert reopened_errors == []
        assert reopened.get_attribute("html", "data-studio-integrity") == "analyst-edited-unverified"
        assert reopened.locator("[data-studio-custom-section]").count() == 1
        assert reopened.get_by_text("Account-team hypothesis to validate").count() == 1
        assert reopened.locator(".studio-user-block img[src^='data:image/svg+xml']").count() == 1
        assert reopened.locator("#forecast").is_hidden()

        # User additions may grow the document, but every source proof and
        # source asset still occurs with the same multiplicity.
        edited = _surfaces(reopened)
        for key in ("hrefs", "images", "edits", "logos"):
            for value in original[key]:
                assert edited[key].count(value) >= original[key].count(value)
        assert edited["evidence"] == original["evidence"]

        edited_context.close()
        browser.close()


def test_corrupt_embedded_state_fails_closed(tmp_path):
    chrome = _chrome()
    if not chrome:
        pytest.skip("no system Chrome available for Editable Studio test")

    compiled = tmp_path / "corrupt-state.html"
    compile_file(
        ROOT / "fixtures/golden/riverbed_golden.html",
        compiled,
        client_name="Riverbed",
        stamp="2026-07-30",
    )
    document = compiled.read_text(encoding="utf-8")
    document = document.replace(
        '<script type="application/json" id="lila-studio-state"></script>',
        '<script type="application/json" id="lila-studio-state">{not-json}</script>',
        1,
    )
    compiled.write_text(document, encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=chrome)
        context, page, errors = _new_page(browser)
        page.goto(compiled.as_uri(), wait_until="load")
        page.wait_for_selector("html[data-studio-boot='error']")
        assert "embedded Studio state is invalid" in (
            page.get_attribute("html", "data-studio-boot-error") or "")
        assert page.locator("[data-action='toggle-studio']").count() == 0
        assert errors == []  # the runtime contains the fault and marks the document
        context.close()
        browser.close()
