"""Insignary golden artifact: the manifest test (2026-07-16).

Renders the board via the press runner's replay path from the REAL stored
inputs (sweep + operator-approved content artifact, both committed) and
asserts every property in tests/fixtures/insignary_golden_manifest.json.

Doctrine: figure values assert against the stored sweep's facts. Federal links
have an independent record-identity gate. The current stored pack backs every
figure but lacks one cited SAM notice GUID, so the replay must render its
DO-NOT-SEND evidence and return the blocking status. Nothing here weakens a
gate or invents the absent primary record.
"""
from datetime import datetime, time, timezone
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import run_signal_board as runner  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "insignary_golden_manifest.json")

_FIG_RE = re.compile(r"\$[0-9][0-9,.]*[MBK]?\b")
_DATE_RE = re.compile(r"\b[0-9]{2} [A-Z]{3} [0-9]{4}\b")
_PUBLIC_OPP_RE = re.compile(r"sam\.gov/opp/[0-9a-f]{32}/view")


def _manifest() -> dict:
    with open(_MANIFEST, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def golden(tmp_path_factory) -> dict:
    """One replay press of the real stored Insignary inputs.

    Module-scoped, so it runs BEFORE the function-scoped conftest env
    isolation: it must isolate LILA_ENTITIES_DIR itself or the press would
    write the operator's real unresolved.log."""
    manifest = _manifest()
    out = str(tmp_path_factory.mktemp("golden") / "render.html")

    class _Args:
        client = manifest["client"]
        replay = True
        render_date = manifest["render_date"]

    _Args.out = out
    prior = os.environ.get("LILA_ENTITIES_DIR")
    os.environ["LILA_ENTITIES_DIR"] = str(tmp_path_factory.mktemp("entities"))
    try:
        code = runner._press(_Args)
    finally:
        if prior is None:
            os.environ.pop("LILA_ENTITIES_DIR", None)
        else:
            os.environ["LILA_ENTITIES_DIR"] = prior
    with open(out, encoding="utf-8") as f:
        html = f.read()
    return {"manifest": manifest, "html": html, "exit_code": code}


def test_stored_pack_backs_every_manifest_figure(golden):
    """The stored sweep independently backs every displayed figure."""
    from agents.reports.board_content import (
        load_board_content, reconcile_figures, reconciliation_error,
    )
    with open(os.path.join(_ROOT, "data", "cleaned",
                           "searches_insignary.json"), encoding="utf-8") as f:
        sweep = json.load(f)
    content = load_board_content("Insignary")
    rec = reconcile_figures(content, sweep)
    if not rec.clean:
        pytest.fail(
            "GOLDEN FIGURE MANIFEST BLOCKED · stored inputs cannot back the "
            "board\n" + reconciliation_error(rec)
            + "\nResolution is the operator's: re-pull the missing award "
              "records into the stored sweep, or attest the affected "
              "figures (analyst_attested + retrieved_at) in "
              "clients/insignary/signal_board_content.json.")


def test_missing_sam_primary_record_blocks_golden_press(golden):
    """The absent ODOS SAM GUID is named and blocks the same replay run."""
    from agents.reports.board_content import (
        load_board_content, reconcile_federal_link_records,
    )

    with open(os.path.join(_ROOT, "data", "cleaned",
                           "searches_insignary.json"), encoding="utf-8") as f:
        sweep = json.load(f)
    content = load_board_content("Insignary")
    render_date = golden["manifest"]["render_date"]
    now = datetime.combine(
        datetime.fromisoformat(render_date).date(),
        time(23, 59, tzinfo=timezone.utc),
    )
    rec = reconcile_federal_link_records(content, sweep, now=now)
    expected_url = (
        "https://sam.gov/opp/"
        "1e9c531df5a841c2935f370db3743213/view"
    )
    assert [(violation.rule, violation.excerpt)
            for violation in rec.violations] == [
        ("FEDERAL_LINK_RECORD_ABSENT", expected_url),
    ]
    assert all(label in rec.violations[0].detail for label in (
        "Signal strip",
        "Best-fit opportunities",
        "Prospective horizon",
    ))
    assert golden["exit_code"] == 2


def test_sections_and_h2_set(golden):
    html, m = golden["html"], golden["manifest"]
    assert html.count("<section") == m["sections"]
    band_h2s = set(re.findall(r'<section class="sb-band"[^>]*>.*?<h2[^>]*>'
                              r"([^<]*)</h2>", html, re.S))
    assert band_h2s == set(m["h2_set"])


def test_dollar_figure_set_is_exact(golden):
    html, m = golden["html"], golden["manifest"]
    rendered = set(_FIG_RE.findall(html))
    assert rendered == set(m["dollar_figure_set"]), (
        f"extra={sorted(rendered - set(m['dollar_figure_set']))} "
        f"missing={sorted(set(m['dollar_figure_set']) - rendered)}")


def test_date_set_and_computed_data_current(golden):
    html, m = golden["html"], golden["manifest"]
    dates = set(_DATE_RE.findall(html))
    for d in m["date_set_includes"]:
        assert d in dates, f"manifest date {d} not rendered"
    assert f"data current {m['data_current']}" in html


def test_link_counts_and_zero_workspace(golden):
    html, m = golden["html"], golden["manifest"]
    assert html.count("usaspending.gov/award") == m["usaspending_award_links"]
    assert len(_PUBLIC_OPP_RE.findall(html)) >= m["public_sam_opp_links_min"]
    assert html.count("sam.gov/workspace") == m["sam_workspace_links"]


def test_hygiene_disclaimer_scripts_emdashes(golden):
    html, m = golden["html"], golden["manifest"]
    assert m["agency_marks_disclaimer"] in html.lower()
    assert html.count("<script") == m["script_tags"]
    assert html.count("—") == m["em_dashes"]


def test_agency_seals_embedded(golden):
    html, m = golden["html"], golden["manifest"]
    embedded = html.count("data:image/png;base64,")
    assert embedded == m["embedded_png_images"], (
        f"embedded PNG images: {embedded} (manifest: "
        f"{m['embedded_png_images']})")

    # The old manifest called every embedded PNG an agency seal, which made
    # client and GTM logo changes look like agency-mark drift. Agency marks
    # are now counted only from their typed render element and pinned by key
    # in render order. This keeps the test fail-closed without conflating
    # unrelated brand assets with official federal marks.
    opening_tags = re.findall(
        r'<[^>]+\bdata-brand-kind="agency"[^>]*>\s*'
        r'<img\b[^>]*\bsrc="data:image/png;base64,',
        html,
    )
    agency_keys = []
    for tag in opening_tags:
        key = re.search(r'\bdata-brand-key="([^"]+)"', tag)
        assert key is not None, f"typed agency mark has no key: {tag[:160]}"
        agency_keys.append(key.group(1))
    assert len(agency_keys) == m["agency_seal_instances"], (
        f"agency seal instances: {len(agency_keys)} (manifest: "
        f"{m['agency_seal_instances']})")
    assert agency_keys == m["agency_seal_keys"]
