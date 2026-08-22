"""Read-only Command Center source-network contract tests."""

from __future__ import annotations

import os

import pytest

flask = pytest.importorskip("flask")

import ui.server as srv
from ui.static import cc_model


def test_source_surface_reconciles_and_is_render_ready():
    """The source wall must remain complete, grouped, and self-reconciling."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    surface = cc_model.build_sources(root, {})
    sources = surface["sources"]
    provenance = surface["provenance"]

    assert sources
    assert provenance["total"] == len(sources)
    assert sum(provenance["group_counts"].values()) == len(sources)
    assert len({source["key"] for source in sources}) == len(sources)
    assert all({
        "key", "name", "coverage", "url", "group", "status", "reason"
    } <= source.keys() for source in sources)
    assert all(source["group"] in provenance["group_counts"] for source in sources)
    assert all(source["status"] in {
        "registered", "catalog-verified", "degraded", "unverified"
    } for source in sources)
    assert not any(source["status"] == "enabled" for source in sources)
    named = {source["key"]: source for source in sources}
    assert named["sam.gov"]["url"] == "https://sam.gov"
    assert named["sam.gov"]["coverage"]
    assert named["usaspending.gov"]["url"] == "https://www.usaspending.gov"
    vehicle_sources = [source for source in sources if source["group"] == "VEHICLES"]
    assert vehicle_sources
    assert any(source["status"] == "unverified" for source in vehicle_sources)
    assert all("verified" in source for source in vehicle_sources)


def test_cc_model_endpoint_preserves_source_network_contract(monkeypatch):
    """Home can load the source wall through the existing selected-client API."""
    expected_sources = [{
        "key": "sam_gov",
        "name": "SAM.gov",
        "coverage": "Contract opportunities",
        "url": "https://sam.gov",
        "group": "PROCUREMENT",
        "mono": False,
        "status": "registered",
        "reason": None,
    }]
    expected_provenance = {
        "source_catalog": {
            "file": "tools/api/source_catalog.py::SOURCE_SPECS",
            "count": 1,
        },
        "group_counts": {"PROCUREMENT": 1},
        "total": 1,
    }
    monkeypatch.setattr(
        cc_model,
        "build_model",
        lambda slug: {
            "client": {"slug": slug, "name": "Test Client"},
            "sources": expected_sources,
            "sources_provenance": expected_provenance,
        },
    )

    response = srv.app.test_client().get("/api/client/test_client/cc-model")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["sources"] == expected_sources
    assert payload["sources_provenance"] == expected_provenance


def test_operator_source_network_endpoint_is_client_independent(monkeypatch):
    expected = {"sources": [{"key": "sam_gov"}], "provenance": {"total": 1}}
    monkeypatch.setattr(cc_model, "build_sources", lambda root, degraded: expected)

    response = srv.app.test_client().get("/api/source-network")

    assert response.status_code == 200
    assert response.get_json() == expected


def test_source_network_endpoint_is_client_independent(monkeypatch):
    """The Command Center can show all sources without a client read model."""
    expected = {
        "sources": [{
            "key": "sam_gov",
            "name": "SAM.gov",
            "coverage": "Contract opportunities",
            "url": "https://sam.gov",
            "group": "PROCUREMENT",
            "mono": False,
            "status": "registered",
            "reason": None,
        }],
        "provenance": {
            "group_counts": {"PROCUREMENT": 1},
            "total": 1,
        },
    }
    calls = []

    def fake_build_sources(root, degraded):
        calls.append((root, degraded))
        return expected

    monkeypatch.setattr(cc_model, "build_sources", fake_build_sources)

    response = srv.app.test_client().get("/api/source-network")

    assert response.status_code == 200
    assert response.get_json() == expected
    assert calls == [(srv.ROOT, {})]


def test_command_center_safe_url_rejects_blank_source_routes():
    """Blank indexed sources must not become links back to the app origin."""
    script = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ui", "static", "command-center-home.js"), encoding="utf-8").read()
    assert 'if (!String(value || "").trim()) return "";' in script


def test_source_network_reserves_animation_for_the_network_beacon():
    """Catalog presence must not masquerade as live source health."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = open(os.path.join(
        root, "ui", "static", "command-center-home.js"), encoding="utf-8").read()
    styles = open(os.path.join(
        root, "ui", "static", "command-center-home.css"), encoding="utf-8").read()

    assert 'cc2-source-dot ' in script
    assert 'cc2-pulse-dot' not in script
    assert '.cc2-source-dot.is-registered' in styles
    assert '.cc2-source-dot.is-degraded' in styles
    assert '.cc2-source-dot.is-unverified' in styles
    assert '.cc2-network-beacon i' in styles
    assert 'animation: cc2-network-wave' in styles
