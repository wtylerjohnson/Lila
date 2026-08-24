"""Production CLI bindings for the fail-closed LILA release action."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import run_lila_release as release_cli
from agents.golden_press.product_bundle import ProductReleaseResult


def test_cli_import_does_not_import_ui_or_scrub_environment():
    script = """
import os
import sys
os.environ['OPENAI_API_KEY'] = 'sentinel-key'
import run_lila_release
assert 'ui.server' not in sys.modules
assert 'flask' not in sys.modules
assert os.environ['OPENAI_API_KEY'] == 'sentinel-key'
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(release_cli.__file__).resolve().parent,
        env={**os.environ, "OPENAI_API_KEY": "sentinel-key"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_target_inventory_loader_propagates_missing_designated_sweep(
        tmp_path, monkeypatch):
    import agents.targeting_inventory as targeting_inventory

    monkeypatch.setattr(
        targeting_inventory,
        "sweep_artifact_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FileNotFoundError("designated sweep is missing")),
    )

    with pytest.raises(FileNotFoundError, match="designated sweep is missing"):
        release_cli._load_current_target_inventory("Acme", tmp_path)


def test_main_passes_live_loader_and_launch_binding(tmp_path, monkeypatch):
    seen = {}

    def build_complete_bundle(**kwargs):
        seen.update(kwargs)
        return ProductReleaseResult(
            release_id="2026-08-24-abc",
            release_dir=str(tmp_path / "release"),
            bundle_path=str(tmp_path / "release.zip"),
            html_path=str(tmp_path / "release.html"),
            manifest_path=str(tmp_path / "manifest.json"),
            manifest_sha256="1" * 64,
            bundle_sha256="2" * 64,
        )

    monkeypatch.setattr(release_cli, "ROOT", tmp_path)
    monkeypatch.setattr(
        release_cli, "build_complete_bundle", build_complete_bundle)
    launch_hash = "a" * 64

    status = release_cli.main([
        "--client", "Acme",
        "--as-of", "2026-08-24",
        "--release",
        "--target-set-sha256", launch_hash,
        "--no-desktop",
    ])

    assert status == 0
    assert seen["root"] == tmp_path
    assert seen["client_name"] == "Acme"
    assert seen["slug"] == "acme"
    assert seen["release_requested"] is True
    assert seen["target_inventory_loader"] is (
        release_cli._load_current_target_inventory)
    assert seen["expected_target_set_sha256"] == launch_hash
    assert seen["deliver_to_desktop"] is False
