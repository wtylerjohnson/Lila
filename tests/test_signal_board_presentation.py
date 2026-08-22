"""Presentation-only logo overrides for every future Signal Board build."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import multiprocessing
import os

import pytest

from agents.reports import signal_board_presentation as presentation


NOW = datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc)


def _png(color=(20, 110, 180, 255)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (24, 16), color).save(out, format="PNG")
    return out.getvalue()


def _store_worker(root: str, label: str, color: tuple, start) -> None:
    start.wait()
    presentation.store_logo_override(
        "Riverbed", kind="company", label=label, raw=_png(color),
        root=root, updated_at=NOW)


def test_identity_keys_are_stable_and_agency_components_share_parent():
    assert presentation.logo_identity(
        "Riverbed", "client", "ignored") == "client:riverbed"
    assert presentation.logo_identity(
        "Riverbed", "company", "The Boeing Company") == "company:boeing"
    assert presentation.logo_identity(
        "Riverbed", "agency", "USCIS") == "agency:dhs"
    assert presentation.logo_identity(
        "Riverbed", "agency", "Department of Homeland Security") == \
        "agency:dhs"
    assert presentation.logo_identity(
        "Riverbed", "agency", "National Transportation Safety Board") == \
        "agency:ntsb"
    assert presentation.logo_identity(
        "Riverbed", "agency", "FHWA") == "agency:dot"
    assert presentation.logo_identity(
        "Riverbed", "agency", "BLM") == "agency:doi"
    assert presentation.logo_identity(
        "Riverbed", "agency", "IRS · OCC") == "agency:treas"


def test_header_companion_copy_is_client_scoped_and_byte_deterministic(
        tmp_path):
    absent_digest = presentation.presentation_digest(
        "Riverbed", root=tmp_path)
    assert presentation.resolve_header_companion_text(
        "Riverbed", root=tmp_path) == \
        presentation.DEFAULT_HEADER_COMPANION_TEXT

    saved = presentation.store_header_companion_text(
        "Riverbed", "  FEDERAL  CAPTURE  PRIORITIES  ", root=tmp_path)
    manifest = presentation.presentation_path("Riverbed", root=tmp_path)
    first_bytes = manifest.read_bytes()
    first_digest = presentation.presentation_digest(
        "Riverbed", root=tmp_path)

    assert saved == "FEDERAL CAPTURE PRIORITIES"
    assert presentation.resolve_header_companion_text(
        "Riverbed", root=tmp_path) == "FEDERAL CAPTURE PRIORITIES"
    assert first_digest != absent_digest
    assert presentation.resolve_header_companion_text(
        "Mark43", root=tmp_path) == \
        presentation.DEFAULT_HEADER_COMPANION_TEXT

    presentation.store_header_companion_text(
        "Riverbed", "FEDERAL CAPTURE PRIORITIES", root=tmp_path)
    assert manifest.read_bytes() == first_bytes
    assert presentation.presentation_digest(
        "Riverbed", root=tmp_path) == first_digest


def test_logo_size_is_identity_scoped_persistent_and_resettable(tmp_path):
    raw = _png()
    override = presentation.store_logo_override(
        "Riverbed", kind="agency", label="Department of the Treasury", raw=raw,
        root=tmp_path, updated_at=NOW)

    saved = presentation.store_logo_size(
        "Riverbed", kind="agency", label="Department of the Treasury",
        percent=145,
        root=tmp_path)
    payload = presentation.load_presentation("Riverbed", root=tmp_path)

    assert saved == {"identity": "agency:treas", "percent": 145}
    assert presentation.resolve_logo_sizes(
        "Riverbed", root=tmp_path) == {"agency:treas": 145}
    assert payload["logo_overrides"]["agency:treas"]["sha256"] == \
        override["sha256"]
    assert presentation.resolve_logo_sizes(
        "Mark43", root=tmp_path) == {}

    presentation.store_logo_size(
        "Riverbed", kind="agency", label="Department of the Treasury",
        percent=100,
        root=tmp_path)
    assert presentation.resolve_logo_sizes("Riverbed", root=tmp_path) == {}
    assert presentation.resolve_logo_override(
        "Riverbed", kind="agency", label="Department of the Treasury",
        root=tmp_path).startswith("data:image/png;base64,")


@pytest.mark.parametrize("value,reason", [
    (49, "between 50 and 200"),
    (201, "between 50 and 200"),
    (111, "5-percent increments"),
    (True, "integer percentage"),
    ("125", "integer percentage"),
])
def test_logo_size_rejects_invalid_values(tmp_path, value, reason):
    with pytest.raises(ValueError, match=reason):
        presentation.store_logo_size(
            "Riverbed", kind="company", label="Example", percent=value,
            root=tmp_path)


def test_presentation_digest_binds_logo_size_without_rewriting_asset(tmp_path):
    row = presentation.store_logo_override(
        "Riverbed", kind="company", label="Example", raw=_png(),
        root=tmp_path, updated_at=NOW)
    base = presentation.presentation_path("Riverbed", root=tmp_path).parent
    asset_before = (base / row["asset"]).read_bytes()
    before = presentation.presentation_digest("Riverbed", root=tmp_path)

    presentation.store_logo_size(
        "Riverbed", kind="company", label="Example", percent=130,
        root=tmp_path)

    assert presentation.presentation_digest("Riverbed", root=tmp_path) != before
    assert (base / row["asset"]).read_bytes() == asset_before


@pytest.mark.parametrize("value,reason", [
    ("line one\nline two", "single line"),
    ("SAFE\u202eTXT", "unsupported characters"),
    ("<script>alert(1)</script>", "markup"),
    ("!?!", "visible words"),
    ("X" * 65, "64-character"),
])
def test_header_companion_copy_rejects_unsafe_or_unusable_content(
        value, reason):
    with pytest.raises(ValueError, match=reason):
        presentation.validate_header_companion_text(value)


def test_content_addressed_override_resolves_and_is_client_scoped(tmp_path):
    raw = _png()
    row = presentation.store_logo_override(
        "Riverbed", kind="company", label="Booz Allen Hamilton", raw=raw,
        root=tmp_path, updated_at=NOW)

    assert row["identity"] == "company:booz_allen_hamilton"
    assert row["sha256"] in row["asset"]
    assert presentation.resolve_logo_override(
        "Riverbed", kind="company", label="Booz Allen Hamilton",
        root=tmp_path).startswith("data:image/png;base64,")
    assert presentation.resolve_logo_override(
        "Mark43", kind="company", label="Booz Allen Hamilton",
        root=tmp_path) == ""


def test_replacement_moves_pointer_without_torn_or_deleted_prior_asset(tmp_path):
    first = presentation.store_logo_override(
        "Riverbed", kind="agency", label="DHS", raw=_png((1, 2, 3, 255)),
        root=tmp_path, updated_at=NOW)
    second = presentation.store_logo_override(
        "Riverbed", kind="agency", label="USCIS", raw=_png((4, 5, 6, 255)),
        root=tmp_path, updated_at=NOW)

    base = presentation.presentation_path("Riverbed", root=tmp_path).parent
    assert first["identity"] == second["identity"] == "agency:dhs"
    assert first["asset"] != second["asset"]
    assert (base / first["asset"]).is_file()
    assert (base / second["asset"]).is_file()
    manifest = presentation.load_presentation("Riverbed", root=tmp_path)
    assert manifest["logo_overrides"]["agency:dhs"]["asset"] == \
        second["asset"]


@pytest.mark.parametrize("raw,reason", [
    (b"not an image", "valid supported image"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
     "SVG uploads"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" onload="go()"/>',
     "SVG uploads"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://x/logo.png"/></svg>',
     "SVG uploads"),
    (b'<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>',
     "SVG uploads"),
])
def test_unsafe_or_unrecognized_uploads_are_rejected(tmp_path, raw, reason):
    with pytest.raises(ValueError, match=reason):
        presentation.store_logo_override(
            "Riverbed", kind="company", label="Example", raw=raw,
            root=tmp_path, updated_at=NOW)


@pytest.mark.parametrize("raw", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><set attributeName="href" to="https://x"/></svg>',
    b'<?xml-stylesheet href="https://x"?><svg xmlns="http://www.w3.org/2000/svg"/>',
    b'<svg xmlns="http://www.w3.org/2000/svg" xml:base="https://x"><image href="#mark"/></svg>',
])
def test_all_svg_uploads_are_rejected_including_indirect_references(raw):
    with pytest.raises(ValueError, match="SVG uploads"):
        presentation.validate_logo_bytes(raw)


def test_tamper_and_path_escape_degrade_to_normal_fallback(tmp_path):
    row = presentation.store_logo_override(
        "Riverbed", kind="company", label="Example", raw=_png(),
        root=tmp_path, updated_at=NOW)
    manifest_path = presentation.presentation_path("Riverbed", root=tmp_path)
    base = manifest_path.parent
    (base / row["asset"]).write_bytes(_png((90, 80, 70, 255)))
    assert presentation.resolve_logo_override(
        "Riverbed", kind="company", label="Example", root=tmp_path) == ""

    payload = json.loads(manifest_path.read_text())
    payload["logo_overrides"][row["identity"]]["asset"] = "../../escape.png"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    assert presentation.resolve_logo_override(
        "Riverbed", kind="company", label="Example", root=tmp_path) == ""


def test_store_refuses_to_overwrite_malformed_or_foreign_manifest(tmp_path):
    path = presentation.presentation_path("Riverbed", root=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"client_slug":"mark43",'
                    '"logo_overrides":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="another client"):
        presentation.store_logo_override(
            "Riverbed", kind="client", label="Riverbed", raw=_png(),
            root=tmp_path, updated_at=NOW)


def test_symlinked_presentation_directory_cannot_escape_client_jail(tmp_path):
    base = presentation.presentation_path("Riverbed", root=tmp_path).parent
    (base / "assets").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, base / "assets" / "presentation")

    with pytest.raises(ValueError, match="symlink"):
        presentation.store_logo_override(
            "Riverbed", kind="company", label="Example", raw=_png(),
            root=tmp_path, updated_at=NOW)
    assert list(outside.iterdir()) == []


def test_cross_process_uploads_preserve_both_manifest_entries(tmp_path):
    context = multiprocessing.get_context("fork")
    start = context.Event()
    workers = [
        context.Process(
            target=_store_worker,
            args=(str(tmp_path), label, color, start),
        )
        for label, color in (
            ("Alpha Federal", (1, 2, 3, 255)),
            ("Beta Federal", (4, 5, 6, 255)),
        )
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    payload = presentation.load_presentation("Riverbed", root=tmp_path)
    assert set(payload["logo_overrides"]) == {
        "company:alpha_federal", "company:beta_federal",
    }


def test_presentation_digest_binds_manifest_and_asset_bytes(tmp_path):
    absent = presentation.presentation_digest("Riverbed", root=tmp_path)
    row = presentation.store_logo_override(
        "Riverbed", kind="client", label="Riverbed", raw=_png(),
        root=tmp_path, updated_at=NOW)
    current = presentation.presentation_digest("Riverbed", root=tmp_path)
    assert current != absent

    base = presentation.presentation_path("Riverbed", root=tmp_path).parent
    (base / row["asset"]).write_bytes(_png((9, 8, 7, 255)))
    with pytest.raises(ValueError, match="hash"):
        presentation.presentation_digest("Riverbed", root=tmp_path)


def test_presentation_digest_parses_the_exact_manifest_bytes_it_hashes(
        tmp_path, monkeypatch):
    presentation.store_logo_override(
        "Riverbed", kind="company", label="Alpha Federal",
        raw=_png((1, 2, 3, 255)), root=tmp_path, updated_at=NOW)
    manifest = presentation.presentation_path("Riverbed", root=tmp_path)
    manifest_a = manifest.read_bytes()
    digest_a = presentation.presentation_digest("Riverbed", root=tmp_path)

    presentation.store_logo_override(
        "Riverbed", kind="company", label="Beta Federal",
        raw=_png((4, 5, 6, 255)), root=tmp_path, updated_at=NOW)
    manifest_b = manifest.read_bytes()
    manifest.write_bytes(manifest_a)

    original = type(manifest).read_bytes
    swapped = False

    def swap_after_read(path):
        nonlocal swapped
        raw = original(path)
        if path == manifest and not swapped:
            swapped = True
            manifest.write_bytes(manifest_b)
        return raw

    monkeypatch.setattr(type(manifest), "read_bytes", swap_after_read)

    assert presentation.presentation_digest(
        "Riverbed", root=tmp_path) == digest_a


def test_size_and_timezone_limits_are_explicit(tmp_path):
    with pytest.raises(ValueError, match="2 MiB"):
        presentation.validate_logo_bytes(b"x" * (
            presentation.MAX_LOGO_BYTES + 1))
    with pytest.raises(ValueError, match="timezone-aware"):
        presentation.store_logo_override(
            "Riverbed", kind="client", label="Riverbed", raw=_png(),
            root=tmp_path, updated_at=datetime(2026, 7, 19))
