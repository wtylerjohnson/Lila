"""The Market Map press leg: canonical-path wiring, fail-closed law, replay.

These tests run the leg the way `golden_press` runs it: from a saved
evidence pack, offline, with no model call. The recorded apexanalytix pack
is real press state, so what certifies here is what certifies in production.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.golden_press.market_map_press import (
    _client_export, _css_balanced, hydrate_market_map_client_mark,
)

_ROOT = Path(__file__).resolve().parents[1]
_PACK = (_ROOT / "data" / "state" / "candidate_review_v1" / "apexanalytix"
         / "apexanalytix.golden_report.evidence_pack.json")
_PROFILE = _ROOT / "clients" / "apexanalytix" / "profile.json"


@pytest.fixture(scope="module")
def pressed(tmp_path_factory, monkeypatch_module=None):
    if not (_PACK.exists() and _PROFILE.exists()):
        pytest.skip("apexanalytix press state not on this checkout")
    from agents.golden_press.market_map_press import press_market_map
    from agents.golden_press.records import EvidencePack

    pack = EvidencePack.model_validate(
        json.loads(_PACK.read_text(encoding="utf-8")))
    out = tmp_path_factory.mktemp("mmpress")
    result = press_market_map(
        pack=pack, client="apexanalytix", slug="apexanalytix",
        root=_ROOT, out_dir=out, stamp="2026-08-07",
        profile=json.loads(_PROFILE.read_text(encoding="utf-8")),
        log=lambda _m: None)
    return result, out


def test_press_certifies_from_the_saved_pack(pressed):
    result, _out = pressed
    assert result["certified"], (
        "the recorded pack must press clean; violations: "
        f"{result['violations'][:4]}")


def test_press_writes_the_artifact_family(pressed):
    result, out = pressed
    stem = result["stem"]
    for suffix in (".html", ".client.html", ".validation.json",
                   ".document.json"):
        assert (out / f"{stem}{suffix}").exists(), f"missing {suffix}"


def test_client_build_carries_no_studio_surface(pressed):
    _result, out = pressed
    client = (out / "apexanalytix.market_map.client.html").read_text(
        encoding="utf-8")
    assert "<script" not in client.lower()
    assert "contenteditable" not in client.lower()
    assert "data-edit-id" not in client
    assert "studio-toolbar" not in client


def test_client_build_preserves_identity_marks_and_evidence_links(pressed):
    """Client export strips editing UI, never the authority or proof layer."""
    _result, out = pressed
    client = (out / "apexanalytix.market_map.client.html").read_text(
        encoding="utf-8")
    assert 'src="data:image/' in client
    assert 'href="https://' in client
    assert "logo-fallback" in client


def test_client_export_removes_whole_studio_rules_and_keeps_css_balanced():
    source = """<!doctype html><style>
    .studio-toolbar { color: red; }
    .kept { color: black; }
    @media print {
      .studio-toolbar, .ticker { display: none; }
      .kept { display: block; }
    }
    </style><div class="studio-toolbar">tools</div><div class="kept">x</div>
    """
    client = _client_export(source)
    styles = re.findall(r"<style[^>]*>(.*?)</style>", client, flags=re.S)

    assert styles and all(_css_balanced(css) for css in styles)
    assert ".studio-toolbar" not in client
    assert ".kept" in client
    assert ".ticker" in client


def test_client_export_converts_every_receipt_button_to_a_native_link():
    source = (
        '<main><button class="edition-meta" type="button" '
        'data-work="report"><time>2026-08-15</time>'
        '<span>12 retained records</span></button></main>')
    receipts = {
        "report": {"title": "Evidence inventory", "summary": "Proof.",
                   "formula": "12 retained records", "sources": []}}

    client = _client_export(source, receipts)

    assert '<a class="edition-meta" ' in client
    assert 'type="button"' not in client
    assert 'href="#receipt-report"' in client
    assert "<button" not in client
    assert 'id="receipt-report"' in client


def test_live_press_hydrates_client_mark_only_when_intake_has_a_site(
        tmp_path, monkeypatch):
    from agents.reports import report_assets
    from tools import brand_marks

    intake = tmp_path / "data" / "intake"
    intake.mkdir(parents=True)
    (intake / "acme.submission.json").write_text(
        json.dumps({"website": "https://acme.example"}), encoding="utf-8")
    state = {"ready": False}
    monkeypatch.setattr(
        report_assets, "client_logo",
        lambda _name: "data:image/png;base64,AA" if state["ready"] else "")
    monkeypatch.setattr(report_assets, "clear_caches", lambda: None)

    def _fetch(_name):
        state["ready"] = True
        return True

    monkeypatch.setattr(brand_marks, "prefetch_client_logo", _fetch)

    assert hydrate_market_map_client_mark(
        client="Acme", slug="acme", root=tmp_path,
        log=lambda _message: None) == "fetched"


def test_pressed_client_stylesheets_are_balanced(pressed):
    _result, out = pressed
    client = (out / "apexanalytix.market_map.client.html").read_text(
        encoding="utf-8")
    styles = re.findall(r"<style[^>]*>(.*?)</style>", client, flags=re.S)
    assert styles and all(_css_balanced(css) for css in styles)


def test_visual_contract_violations_prevent_press_certification(
        tmp_path, monkeypatch):
    """A data-clean press with a blank client mark or inert control fails."""
    from agents.golden_press import (
        market_map_projection, market_map_render, market_map_skeleton,
        market_map_validate, style_contract,
    )
    from agents.golden_press.market_map_press import press_market_map

    doc = SimpleNamespace(
        receipts={}, research_demand=(), qualified_opportunities=(),
        contact_actions=(), events=(),
        competitive_position=SimpleNamespace(competitors=()),
    )
    monkeypatch.setattr(market_map_projection, "build_market_map",
                        lambda *_args, **_kwargs: doc)
    monkeypatch.setattr(market_map_projection, "capture_inputs",
                        lambda **_kwargs: {})
    monkeypatch.setattr(market_map_render, "coverage_items", lambda _doc: [])
    monkeypatch.setattr(market_map_render, "render_sections",
                        lambda _doc, _ids: "")
    monkeypatch.setattr(market_map_render, "ticker_items", lambda _doc: [])
    monkeypatch.setattr(market_map_render, "work_details", lambda _doc: {})
    monkeypatch.setattr(
        market_map_skeleton, "build_document",
        lambda **_kwargs: (
            '<!doctype html><html><head><style>.client-mark,.gtm-mark {'
            'display:block}</style></head><body>'
            '<div class="client-mark"><span>AC</span></div>'
            '<svg class="gtm-mark" width="10" height="10" '
            'xmlns="http://www.w3.org/2000/svg"></svg>'
            '<button type="button">Save HTML</button></body></html>'))
    monkeypatch.setattr(market_map_validate, "validate_market_map",
                        lambda _html: {"ok": True, "violations": [],
                                       "receipts": {}})
    monkeypatch.setattr(style_contract, "unstyled_in_context",
                        lambda _html: [])
    pack = SimpleNamespace(client_name="Acme", records=[])

    result = press_market_map(
        pack=pack, client="Acme", slug="acme", root=tmp_path,
        out_dir=tmp_path, stamp="2026-08-15", profile={},
        log=lambda _message: None)

    rules = {row["rule"] for row in result["violations"]}
    assert result["certified"] is False
    assert {"missing_client_mark", "button_without_action"}.issubset(rules)
    receipt = json.loads(
        (tmp_path / "acme.market_map.validation.json").read_text(
            encoding="utf-8"))
    assert receipt["client_visual_contract"]["ok"] is False


def test_replay_is_byte_identical(pressed, tmp_path):
    """Pack + captured inputs -> the same bytes, always.

    The capture sidecar IS the store as of press time; replay consumes it
    instead of the live stores, so the notice store ingesting a new day or
    the targets store being enriched can never change a sealed document.
    """
    from agents.golden_press.market_map_press import press_market_map
    from agents.golden_press.records import EvidencePack

    result, out = pressed
    first = (out / "apexanalytix.market_map.html").read_text(encoding="utf-8")
    captured = json.loads(Path(result["inputs_path"]).read_text(
        encoding="utf-8"))
    pack = EvidencePack.model_validate(
        json.loads(_PACK.read_text(encoding="utf-8")))
    again = press_market_map(
        pack=pack, client="apexanalytix", slug="apexanalytix",
        root=_ROOT, out_dir=tmp_path, stamp="2026-08-07",
        profile=json.loads(_PROFILE.read_text(encoding="utf-8")),
        inputs=captured, log=lambda _m: None)
    second = Path(again["html_path"]).read_text(encoding="utf-8")
    assert first == second
    assert again["certified"] == result["certified"]


def test_uncertified_press_parks_a_failed_copy(tmp_path):
    """A violating document writes FAILED beside itself and never certifies."""
    if not (_PACK.exists() and _PROFILE.exists()):
        pytest.skip("apexanalytix press state not on this checkout")
    from agents.golden_press import market_map_press as mod
    from agents.golden_press.records import EvidencePack

    pack = EvidencePack.model_validate(
        json.loads(_PACK.read_text(encoding="utf-8")))
    # Force a violation through the lint seam: a banned term in the render.
    original = mod._lint

    def _sabotage(html):
        return original(html) + [{"rule": "test_injected",
                                  "detail": "forced violation"}]

    mod._lint = _sabotage
    try:
        result = mod.press_market_map(
            pack=pack, client="apexanalytix", slug="apexanalytix",
            root=_ROOT, out_dir=tmp_path, stamp="2026-08-07",
            profile=json.loads(_PROFILE.read_text(encoding="utf-8")),
            log=lambda _m: None)
    finally:
        mod._lint = original
    assert not result["certified"]
    assert (tmp_path / "apexanalytix.market_map.FAILED.html").exists()


def test_parity_harness_reads_both_documents(pressed):
    """The Gate A harness: benchmark contract vs the pressed artifact."""
    from tools.reference_contract import extract, parity

    fixture = _ROOT / "fixtures" / "reference" / "golden_87.contract.json"
    if not fixture.exists():
        pytest.skip("benchmark contract fixture absent")
    _result, out = pressed
    ref = json.loads(fixture.read_text(encoding="utf-8"))
    cand = extract((out / "apexanalytix.market_map.html").read_text(
        encoding="utf-8"))
    verdict = parity(ref, cand)
    # The full pass is the gauntlet's exit condition, not this test's; what
    # is protected HERE is the floor already won, so it can never regress.
    fatal = [f for f in verdict["findings"] if f["severity"] == "fatal"]
    assert not fatal, fatal
    assert cand["evidence"]["source_links"] > 0
    assert cand["editing"]["edit_ids"] > 0
    assert cand["navigation"]["nav_present"]
    assert cand["ticker"]["present"]


def test_benchmark_fixture_hash_is_bound():
    """The reference is hash-bound; a drifted fixture fails loudly."""
    import hashlib

    fixture = _ROOT / "fixtures" / "reference" / "golden_87.html"
    stated = _ROOT / "fixtures" / "reference" / "golden_87.sha256"
    if not fixture.exists():
        pytest.skip("benchmark fixture absent")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert digest == stated.read_text(encoding="utf-8").strip()
