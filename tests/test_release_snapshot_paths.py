"""Portable path normalization and outgoing release leak detection."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from agents.golden_press import product_bundle, release_compiler
from agents.golden_press.release_compiler import ReleaseCompilationError
from agents.golden_press.release_snapshot import (
    canonicalize_release_value,
    contains_local_path,
)


@pytest.mark.parametrize(("raw", "expected"), (
    ("/private/var/folders/run/receipt.json", "local-artifact:receipt.json"),
    ("/tmp/lila/output.json", "local-artifact:output.json"),
    ("/Volumes/Research/cache/store.db", "local-artifact:store.db"),
    (r"C:\Users\operator\release\product.json",
     "local-artifact:product.json"),
    (r"D:/build/release/manifest.json", "local-artifact:manifest.json"),
    (r"\\fileserver\capture\jtg\evidence.json",
     "local-artifact:evidence.json"),
    ("//fileserver/capture/jtg/graph.json", "local-artifact:graph.json"),
    ("file://fileserver/capture/jtg/bundle.zip",
     "local-artifact:bundle.zip"),
))
def test_canonicalization_normalizes_supported_local_path_families(
        tmp_path, raw, expected):
    assert canonicalize_release_value(raw, root=tmp_path) == expected


def test_canonicalization_keeps_paths_under_the_capture_root_relative(tmp_path):
    local = tmp_path / "data" / "state" / "graph.json"

    assert canonicalize_release_value(local, root=tmp_path) == \
        "data/state/graph.json"
    assert canonicalize_release_value(str(local), root=tmp_path) == \
        "data/state/graph.json"


@pytest.mark.parametrize("url", (
    "https://agency.gov/Users/public/notice",
    "https://agency.gov/home/opportunities",
    "https://agency.gov/private/acquisition-plan.pdf",
    "https://agency.gov/tmp/download.json",
    "https://agency.gov/Volumes/archive/report.pdf",
    "https://agency.gov/C:/tmp/report.pdf",
    "https://agency.gov/redirect?next=//fileserver/share/report.pdf",
))
def test_http_urls_are_neither_normalized_nor_reported_as_local(
        tmp_path, url):
    assert canonicalize_release_value(url, root=tmp_path) == url
    assert contains_local_path(url) is False
    payload = json.dumps({"source_url": url}).encode("utf-8")
    assert contains_local_path(payload) is False
    assert product_bundle._local_path_leaks({"source.json": payload}) == []
    release_compiler._refuse_local_paths({"source.json": payload})


@pytest.mark.parametrize("local_path", (
    "/Users/operator/release/product.json",
    "/home/operator/release/product.json",
    "/private/var/folders/run/product.json",
    "/tmp/lila/product.json",
    "/Volumes/Research/product.json",
    r"C:\Users\operator\release\product.json",
    r"D:/build/release/product.json",
    r"\\fileserver\capture\jtg\product.json",
    "//fileserver/capture/jtg/product.json",
    "file:///private/tmp/lila/product.json",
    "file://fileserver/capture/jtg/product.json",
))
def test_compiler_and_materializer_reject_every_local_path_family(local_path):
    payload = json.dumps({"path": local_path}).encode("utf-8")

    assert contains_local_path(payload) is True
    assert product_bundle._local_path_leaks({"artifact.json": payload}) == [
        "artifact.json"]
    with pytest.raises(ReleaseCompilationError, match="local filesystem path"):
        release_compiler._refuse_local_paths({"artifact.json": payload})


def test_archive_scan_uses_the_same_windows_and_url_safe_boundary(tmp_path):
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "safe.json",
            json.dumps({"source": "https://agency.gov/tmp/notice"}),
        )
        archive.writestr(
            "leaking.json",
            json.dumps({"path": r"\\fileserver\share\artifact.json"}),
        )

    assert product_bundle._archive_local_path_leaks(archive_path) == [
        "leaking.json"]


def test_archive_member_name_cannot_be_a_posix_workstation_path(tmp_path):
    archive_path = Path(tmp_path) / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("/private/tmp/leak.json", "{}")

    assert product_bundle._archive_local_path_leaks(archive_path) == [
        "/private/tmp/leak.json"]
