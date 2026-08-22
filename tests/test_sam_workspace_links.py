"""Regression coverage for the client-facing SAM workspace-link gate."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from agents.reports.links import (
    build_sam_notice_link,
    normalize_sam_workspace_links,
    verify_rewritten_sam_links,
)
from agents.reports.lint import (
    SAM_WORKSPACE_LINK_RULE_TEXT,
    lint_sam_workspace_links,
)
import tools.export_pdf as export_pdf


FIXTURE = Path(__file__).parent / "fixtures" / "insignary_sam_workspace_links.html"
WORKSPACE_PREFIX = "https://sam.gov/workspace/contract/opp/"
PUBLIC_PREFIX = "https://sam.gov/opp/"
INSIGNARY_GUIDS = (
    "b208e33b8dcd49088d751842a5f462aa",
    "ae337f5d01e3499b980d347fa7567323",
    "b208e33b8dcd49088d751842a5f462aa",
    "b208e33b8dcd49088d751842a5f462aa",
    "49ca8b1859c74fe28a4e9d3ae1c83d68",
    "692f3f7143d84b2da163ab1bcdd73b0b",
    "ae337f5d01e3499b980d347fa7567323",
    "b208e33b8dcd49088d751842a5f462aa",
)
VALID_GUID = "0123456789abcdef0123456789abcdef"


def test_insignary_fixture_rewrites_all_eight_and_nothing_else():
    source = FIXTURE.read_text(encoding="utf-8")
    fixture_guids = tuple(
        re.findall(
            re.escape(WORKSPACE_PREFIX) + r"([0-9a-f]{32})/view",
            source,
        )
    )
    assert fixture_guids == INSIGNARY_GUIDS

    result = normalize_sam_workspace_links(source)

    expected = source.replace(WORKSPACE_PREFIX, PUBLIC_PREFIX)
    assert result.html.encode() == expected.encode()
    assert tuple(item.source_url for item in result.rewrites) == tuple(
        f"{WORKSPACE_PREFIX}{guid}/view" for guid in INSIGNARY_GUIDS
    )
    assert tuple(item.public_url for item in result.rewrites) == tuple(
        f"{PUBLIC_PREFIX}{guid}/view" for guid in INSIGNARY_GUIDS
    )
    assert len(result.rewrites) == 8
    assert result.remaining == ()


@pytest.mark.parametrize(
    ("url", "section"),
    (
        (
            "https://sam.gov/workspace/contract/opp/not-a-hex-guid/view",
            "Malformed notice",
        ),
        (
            f"{WORKSPACE_PREFIX}{VALID_GUID}/archive/view",
            "Wrong path depth",
        ),
        (
            f"{WORKSPACE_PREFIX}{VALID_GUID}/view/extra",
            "Extra path segment",
        ),
    ),
)
def test_malformed_workspace_url_hard_fails_before_chrome(
    tmp_path,
    monkeypatch,
    url,
    section,
):
    html_path = tmp_path / "malformed.assessment.client.html"
    html_path.write_text(
        f'<section id="bad"><h2>{section}</h2><a href="{url}">SAM</a></section>',
        encoding="utf-8",
    )
    chrome_calls = []

    def never_run_chrome(*args, **kwargs):
        chrome_calls.append((args, kwargs))
        raise AssertionError("Chrome ran before the SAM workspace hard gate")

    monkeypatch.setattr(export_pdf.subprocess, "run", never_run_chrome)

    pdf_path, violations = export_pdf.export_deliverable(
        str(html_path), link_checks_enabled=False,
        expected_federal_links=(
            build_sam_notice_link(VALID_GUID, reconciled=True),
        ))

    assert pdf_path is None
    assert len(violations) == 1
    assert violations[0].rule == "sam_workspace_link"
    assert section in violations[0].detail
    assert url in violations[0].detail
    assert violations[0].excerpt == url
    assert chrome_calls == []
    assert not list(tmp_path.glob("*.pdf"))


def test_public_sam_links_pass_untouched():
    source = (
        '<section><h2>Public notices</h2>'
        f'<a href="{PUBLIC_PREFIX}{VALID_GUID}/view">One</a>'
        '<a href="https://sam.gov/opp/ffffffffffffffffffffffffffffffff/view">'
        "Two</a></section>"
    )

    result = normalize_sam_workspace_links(source)

    assert result.html == source
    assert result.rewrites == ()
    assert result.remaining == ()
    assert lint_sam_workspace_links(result.html).ok


@pytest.mark.parametrize(
    "client_html",
    (
        "<html><!-- hand edit: sam.gov/workspace --></html>",
        '<meta name="source" content="https://sam.gov/workspace">',
        "<p>Generated copy mentions sam.gov/workspace without an href.</p>",
    ),
)
def test_client_html_lint_rejects_raw_workspace_substring_anywhere(client_html):
    result = lint_sam_workspace_links(client_html)

    assert not result.ok
    assert {violation.rule for violation in result.violations} == {
        "sam_workspace_link"
    }
    assert SAM_WORKSPACE_LINK_RULE_TEXT == (
        "SAM.gov workspace links require login; client HTML must use public "
        "https://sam.gov/opp/{guid}/view URLs"
    )


def test_release_gate_includes_workspace_lint_for_hand_edits():
    violations = export_pdf.run_gates(
        "<html><!-- hand edit: sam.gov/workspace --></html>"
    )

    assert "sam_workspace_link" in {violation.rule for violation in violations}


def test_normalization_is_idempotent():
    source = FIXTURE.read_text(encoding="utf-8")
    once = normalize_sam_workspace_links(source).html
    twice = normalize_sam_workspace_links(once).html

    assert twice == once


def test_mixed_case_href_preserves_query_and_fragment():
    source = (
        f'<A HREF="http://sam.gov/workspace/contract/opp/{VALID_GUID}/view'
        '?mode=full#attachments">Notice</A>'
    )

    result = normalize_sam_workspace_links(source)

    assert result.html == (
        f'<A HREF="{PUBLIC_PREFIX}{VALID_GUID}/view'
        '?mode=full#attachments">Notice</A>'
    )
    assert len(result.rewrites) == 1
    assert result.remaining == ()


def test_data_href_is_not_rewritten_and_remains_a_hard_failure():
    workspace_url = f"{WORKSPACE_PREFIX}{VALID_GUID}/view"
    source = f'<div data-href="{workspace_url}">Notice</div>'

    result = normalize_sam_workspace_links(source)

    assert result.html == source
    assert result.rewrites == ()
    assert len(result.remaining) == 1
    assert result.remaining[0].url == workspace_url


def test_non_sam_and_non_workspace_links_are_byte_identical():
    source = "\n".join(
        (
            '<a href="https://www.usaspending.gov/award/ABC">USAspending</a>',
            '<a href="https://www.darpa.mil/research/programs/e-boss">DARPA</a>',
            '<a href="https://www.cisa.gov/kev">CISA</a>',
            '<a href="https://www.sewp.nasa.gov/sewpvi/">SEWP</a>',
            '<a href="https://www.govinfo.gov/app/details/FR-2025">GovInfo</a>',
            '<a href="https://example.com/workspace/contract/opp/x/view">Other</a>',
            f'<a href="{PUBLIC_PREFIX}{VALID_GUID}/view">Public SAM</a>',
            '<a href="https://sam.gov/content/home">SAM content</a>',
        )
    )

    result = normalize_sam_workspace_links(source)

    assert result.html.encode() == source.encode()
    assert result.rewrites == ()
    assert result.remaining == ()


def test_exporter_persists_normalized_html_before_rendering(
    tmp_path,
    monkeypatch,
):
    workspace_url = f"{WORKSPACE_PREFIX}{VALID_GUID}/view"
    public_url = f"{PUBLIC_PREFIX}{VALID_GUID}/view"
    html_path = tmp_path / "good.assessment.client.html"
    html_path.write_text(
        f'<section><h2>Notice</h2><a href="{workspace_url}" '
        'data-link-builder="sam_notice" '
        f'data-link-record="{VALID_GUID}" '
        'data-link-reconciled="1">SAM</a></section>',
        encoding="utf-8",
    )
    rendered_html = []

    monkeypatch.setattr(export_pdf, "run_gates", lambda html_text: [])

    def fake_export(path, pdf_path=None, chrome=None):
        rendered_html.append(Path(path).read_text(encoding="utf-8"))
        return str(tmp_path / "good.assessment.client.pdf")

    monkeypatch.setattr(export_pdf, "export_pdf", fake_export)

    pdf_path, violations = export_pdf.export_deliverable(
        str(html_path), link_checks_enabled=False,
        expected_federal_links=(
            build_sam_notice_link(VALID_GUID, reconciled=True),
        ))

    assert violations == []
    assert pdf_path == str(tmp_path / "good.assessment.client.pdf")
    assert len(rendered_html) == 1
    assert public_url in rendered_html[0]
    assert workspace_url not in rendered_html[0]
    assert html_path.read_text(encoding="utf-8") == rendered_html[0]


@pytest.mark.parametrize("api_key", (None, "configured-test-key"))
def test_sam_verifier_compatibility_shim_never_uses_http(monkeypatch, api_key):
    from tools.api import _http

    def never_get_json(*args, **kwargs):
        raise AssertionError("SAM HTTP verification is forbidden")

    monkeypatch.setattr(_http, "get_json", never_get_json)

    public_url = f"{PUBLIC_PREFIX}{VALID_GUID}/view"
    assert verify_rewritten_sam_links(
        [public_url], api_key=api_key
    ) == ()


def test_deterministic_fallback_normalizes_without_sam_http(
    tmp_path,
    monkeypatch,
):
    import agents.reports.document as document
    import agents.reports.links as links
    import agents.reports.views as views
    import run_capture_brief as runner

    reports = tmp_path / "reports"
    searches = tmp_path / "searches.json"
    searches.write_text(json.dumps({"results": {}}), encoding="utf-8")
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    monkeypatch.setattr(
        document, "build_document", lambda *args, **kwargs: object()
    )
    workspace_url = f"{WORKSPACE_PREFIX}{VALID_GUID}/view"
    public_url = f"{PUBLIC_PREFIX}{VALID_GUID}/view"
    monkeypatch.setattr(
        views,
        "render_assessment",
        lambda *args, **kwargs: (
            "<html><body><section><h2>Fallback notice</h2>"
            f'<a href="{workspace_url}">SAM</a></section></body></html>'
        ),
    )
    monkeypatch.setattr(
        links, "verify_rewritten_sam_links",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback called forbidden SAM verifier")),
    )

    assert runner._write_deterministic_draft(
        "Insignary", str(searches), "insignary.fallback", "test fallback"
    ) == 0

    output = reports / "insignary.fallback.html"
    rendered = output.read_text(encoding="utf-8")
    assert workspace_url not in rendered
    assert public_url in rendered
    assert not (reports / "insignary.fallback.internal.md").exists()
