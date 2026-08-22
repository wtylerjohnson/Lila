"""Editable Studio compiler contract.

The Studio is allowed to add authoring machinery. It is not allowed to alter
the evidence-bearing document that reached it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.golden_press.studio import (
    RUNTIME_ID,
    STATE_ID,
    STYLE_ID,
    StudioCompileError,
    compile_editable_studio,
    compile_file,
    inventory,
)


ROOT = Path(__file__).resolve().parents[1]


def _minimal_source(*, duplicate_edit_id: bool = False) -> str:
    repeated = '<span data-edit-id="headline">duplicate</span>' if duplicate_edit_id else ""
    return f'''<!doctype html>
<html lang="en" data-report-id="acme-report" data-download-name="Acme_Report.html">
<head><meta charset="utf-8"><title>Acme · Federal Opportunity Pre-Assessment</title></head>
<body>
  <div class="report-tools" data-editor-ui>
    <button data-action="toggle-edit">Edit</button>
    <button data-action="save-draft">Save</button>
    <button data-action="download-html">Download</button>
  </div>
  <main id="signal-board">
    <header class="sb-utility"><span class="sb-brand-word">ACME</span></header>
    <div id="board">><h1 data-edit-id="headline">Assessment</h1>{repeated}</section>
    <section class="band" id="current"><h2 data-edit-id="current-title">Current</h2>
      <a class="sb-evidence-row" data-source-link href="https://www.usaspending.gov/award/ABC">
        <span class="sb-evidence-record">ABC</span></a>
      <div data-logo-slot-id="current-seal"><img src="data:image/png;base64,AA" alt=""></div>
    </section>
    <aside class="sb-recommended"><span data-edit-id="next-step">Call the buyer.</span></aside>
    <footer class="sb-footer">Evidence first.</footer>
  </main>
</body></html>'''


def test_compiler_is_client_neutral_and_preserves_every_source_surface():
    source = _minimal_source()
    build = compile_editable_studio(
        source, client_name="Acme Networks", stamp="2026-08-01")
    before = inventory(source)
    after = inventory(build.html)

    assert before.hrefs == after.hrefs
    assert before.image_sources == after.image_sources
    assert before.edit_ids == after.edit_ids
    assert before.logo_ids == after.logo_ids
    assert before.evidence_rows == after.evidence_rows == 1
    assert all(build.receipt["preserved"].values())
    assert build.receipt["ready"] is True
    assert 'data-studio-client="Acme Networks"' in build.html
    assert 'data-report-id="acme-networks-federal-opportunity-editable-studio-2026-08-01"' in build.html
    assert 'data-download-name="Acme_Report_EDITABLE_STUDIO.html"' in build.html
    assert f'data-studio-source-sha256="{build.receipt["source_sha256"]}"' in build.html
    assert 'data-studio-integrity="pristine"' in build.html
    assert "Riverbed" not in build.html
    for marker in (STYLE_ID, STATE_ID, RUNTIME_ID):
        assert after.ids[marker] == 1


def test_real_golden_skeleton_compiles_without_losing_links_assets_or_edit_ids():
    source = (ROOT / "fixtures/golden/riverbed_golden.html").read_text(encoding="utf-8")
    build = compile_editable_studio(
        source, client_name="Riverbed", stamp="2026-07-30")
    assert all(build.receipt["preserved"].values())
    assert build.receipt["source"]["hrefs"] == build.receipt["output"]["hrefs"]
    assert build.receipt["source"]["image_sources"] == build.receipt["output"]["image_sources"]
    assert build.receipt["source"]["edit_ids"] == build.receipt["output"]["edit_ids"]
    assert build.receipt["source"]["logo_ids"] == build.receipt["output"]["logo_ids"]


def test_compilation_is_not_silently_applied_twice():
    first = compile_editable_studio(
        _minimal_source(), client_name="Acme", stamp="2026-08-01")
    with pytest.raises(StudioCompileError, match="already contains"):
        compile_editable_studio(
            first.html, client_name="Acme", stamp="2026-08-01")


def test_duplicate_editable_or_logo_ids_fail_before_delivery():
    with pytest.raises(StudioCompileError, match="duplicate editable field ids"):
        compile_editable_studio(
            _minimal_source(duplicate_edit_id=True),
            client_name="Acme", stamp="2026-08-01")


def test_compile_file_writes_output_and_machine_readable_receipt(tmp_path):
    source_path = tmp_path / "source.html"
    output_path = tmp_path / "studio.html"
    receipt_path = tmp_path / "studio.receipt.json"
    source_path.write_text(_minimal_source(), encoding="utf-8")

    receipt = compile_file(
        source_path, output_path,
        client_name="Acme", stamp="2026-08-01",
        receipt_path=receipt_path)

    assert output_path.is_file()
    assert receipt_path.is_file()
    stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert stored == receipt
    assert stored["output_sha256"]
    assert stored["studio_markers"] == {
        STYLE_ID: 1,
        STATE_ID: 1,
        RUNTIME_ID: 1,
    }


@pytest.mark.parametrize(
    "missing,expected",
    [
        (' id="signal-board"', "#signal-board"),
        (" data-editor-ui", "editor chrome"),
        (' data-action="download-html"', "download-html"),
    ],
)
def test_missing_editor_contract_fails_loudly(missing, expected):
    source = _minimal_source().replace(missing, "", 1)
    with pytest.raises(StudioCompileError, match=expected):
        compile_editable_studio(source, client_name="Acme")


def test_press_native_mode_injects_no_runtime_and_keeps_one_editor():
    """2026-08-04 consolidation: the press ships ONE editing system (the
    skeleton's own inherited editor). Native mode adds zero scripts and zero
    styles; the receipt still certifies preservation."""
    source = _minimal_source()
    build = compile_editable_studio(
        source, client_name="Acme Networks", stamp="2026-08-01",
        inject_runtime=False)
    after = inventory(build.html)
    for marker in (STYLE_ID, STATE_ID, RUNTIME_ID):
        assert after.ids.get(marker, 0) == 0
    assert build.html.count("<script") == source.count("<script")
    assert build.html.count("<style") == source.count("<style")
    assert build.receipt["runtime"] == "layout-native"
    assert build.receipt["studio_markers"] == {
        STYLE_ID: 0, STATE_ID: 0, RUNTIME_ID: 0}
    assert all(build.receipt["preserved"].values())
    assert build.receipt["ready"] is True
    assert 'data-studio-integrity="pristine"' in build.html
    # A native output is still recognized as compiled: no silent re-compile.
    with pytest.raises(StudioCompileError, match="already contains"):
        compile_editable_studio(
            build.html, client_name="Acme Networks", stamp="2026-08-01")
