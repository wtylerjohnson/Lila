"""White-label: no vendor/tooling terms in any rendered deliverable.
Blocklist gate + the two narrow exemptions (base64 payloads, cited news
title fields) + a sweep over every renderer's real output."""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.lint import lint_whitelabel  # noqa: E402


def test_blocklist_catches_vendor_terms_everywhere_visible():
    for leak in ("Report drafted by Claude for GTM Group",
                 "<!-- generated with Anthropic tooling -->",
                 "an AI-generated market summary",
                 "our LLM screened 300 notices",
                 "escalated to GPT-4o for arbitration",
                 "set ANTHROPIC_API_KEY before running"):
        r = lint_whitelabel(f"<html><body><p>{leak}</p></body></html>")
        assert not r.ok, leak
        assert r.violations[0].rule == "vendor_term_in_deliverable"


def test_base64_payloads_never_scanned():
    # the GTM logo's base64 famously contains 'gpt' by accident of encoding
    page = ('<html><body><img id="gtmLogoImg" '
            f'src="data:image/png;base64,{"gptAnthropicXYZ" * 20}">'
            "<p>Clean copy.</p></body></html>")
    assert lint_whitelabel(page).ok


def test_cited_news_title_exempt_but_prose_gated():
    # third-party market news naming a vendor: allowed in the cited title field
    ok = ('<html><body><table><tr><td>'
          '<a data-thirdparty="1" href="https://news.example/x">'
          "Booz Allen and OpenAI partner on agency AI</a></td></tr></table>"
          "</body></html>")
    assert lint_whitelabel(ok).ok
    # the same words in composed prose: gated
    bad = ("<html><body><p>Following the Booz Allen and OpenAI partnership, "
           "your lane tightens.</p></body></html>")
    assert not lint_whitelabel(bad).ok


def test_every_renderer_output_is_clean():
    """Sweep the real renderers: three assessment views, the legacy assessment,
    the target report, and the markdown chain."""
    from agents.reports.document import build_document
    from agents.reports.views import render_assessment
    from agents.reports.target_report import render_target_report
    from tests.test_assessment_document import _searches
    from tests.test_capture_brief import _content
    from agents.reports.capture_brief import render_capture_brief

    doc = build_document("Testco", searches=_searches(3), qualify=None,
                         as_of=date(2026, 7, 6))
    outputs = {
        "assessment.client": render_assessment(doc, "client"),
        "assessment.sales": render_assessment(doc, "sales"),
        "assessment.internal": render_assessment(doc, "internal"),
        "legacy_assessment": render_capture_brief(_content()),
        "target_report": render_target_report({
            "client_name": "Testco", "strategy_note": "angle",
            "known_pocs": [], "searches": [], "citations": []}),
    }
    from agents.reports.render import render_markdown
    from agents.reports.build import build_report
    from tests.test_reports import FakeEngine, _pack
    from agents.reports.schemas import ReportKind
    bundle = build_report(_pack(), ReportKind.TEASER, engine=FakeEngine())
    outputs["teaser_md"] = render_markdown(bundle, _pack())

    for name, out in outputs.items():
        r = lint_whitelabel(out)
        assert r.ok, (name, [v.detail for v in r.violations][:3])


def test_regenerated_artifacts_on_disk_are_clean():
    """The research-picture markdowns that used to emit vendor branding."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for slug in ("thinklogical", "recorded_future"):
        p = os.path.join(root, "data", "review", f"{slug}.research_picture.md")
        if not os.path.exists(p):
            continue
        r = lint_whitelabel(open(p).read())
        assert r.ok, (slug, [v.detail for v in r.violations][:3])
