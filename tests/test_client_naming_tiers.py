"""Approved legacy-artifact naming map + terminology lint.

"Federal Opportunity Pre-Assessment" remains the locked Signal Board
compatibility title in every tier. "capture brief" stays banned everywhere
client-facing, every tier, no exceptions. These names do not define the
external eight-slot product contract.
"""
from agents.reports.lint import (
    APPROVED_CLIENT_TITLES,
    lint_client_terminology,
)


def test_naming_map_tiers():
    for tier in ("assessment", "teaser"):
        assert "Federal Opportunity Assessment" in APPROVED_CLIENT_TITLES[tier]
        assert "Federal Opportunity Pre-Assessment" in APPROVED_CLIENT_TITLES[tier]


def test_capture_brief_rejected_everywhere_client_facing():
    html = "<h1>Acme · Capture Brief</h1>"
    for tier in ("assessment", "teaser"):
        result = lint_client_terminology(html, tier=tier)
        assert not result.ok
        assert result.violations[0].rule == "internal_term_in_client_copy"


def test_signal_board_title_accepted_in_every_tier():
    html = "<title>Acme · Federal Opportunity Pre-Assessment</title>"
    assert lint_client_terminology(html, tier="teaser").ok
    assert lint_client_terminology(html).ok


def test_flagship_title_accepted_in_every_tier():
    html = "<title>Acme · Federal Opportunity Assessment</title>"
    assert lint_client_terminology(html).ok
    assert lint_client_terminology(html, tier="teaser").ok


def test_unknown_tier_keeps_the_permanent_public_title():
    html = "<title>Acme · Federal Opportunity Pre-Assessment</title>"
    assert lint_client_terminology(html, tier="no-such-tier").ok
