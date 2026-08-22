"""Aesthetic package install (client-approved Riverbed rework, 2026-08-03).

tokens + seals live in data/reference/; report.css + report_templates.py sit
beside the render code; render draws marks through the resolver (official
mark or monogram, never empty) and receipts every gap in the method band;
styles come only from the css/tokens pair. Supersedes Playfair/IBM Plex.

Offline; no network, no LLM.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import report_templates as rt  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402


def _pack(records):
    return EvidencePack(
        client_name="Acme Networks", generated_at="2026-08-01T12:00:00Z",
        records=records,
        research={"entities": {"product": ["AcmeFlow"], "competitor": []}})


def _award(record_id, *, agency, sub=None, recipient="FCN, INC."):
    return GoldenRecord(
        record_id=record_id, lane="L2_entity_award",
        title=f"Enterprise agreement {record_id.lower()}",
        agency=agency, sub_agency=sub, recipient=recipient,
        entity_hits=["AcmeFlow"], obligated_dollars=1_000_000.0,
        period_end="2026-12-31",
        url=f"https://www.usaspending.gov/award/CONT_AWD_{record_id}")


# --------------------------------------------------------------------------- #
# the installed files
# --------------------------------------------------------------------------- #
def test_tokens_and_seals_are_installed_and_versioned():
    tokens = rt.design_tokens()
    assert tokens.get("version") == 1
    assert "Supersedes Playfair/IBM Plex" in tokens.get("source", "")
    assert tokens["laws"]["mark-slot-never-empty"] is True
    assert tokens["laws"]["no-em-dashes"] is True
    assert tokens["laws"]["position-derived-band-numbers"] is True
    assert "Avenir Next" in tokens["type"]["stack"]
    entries = rt.seal_entries()
    # 24 shipped with the package; the 2026-08-04 coverage
    # round grows it. The floor pins the package, not a cap.
    assert len(entries) >= 24
    assert all(v.get("src", "").startswith("data:image/")
               for v in entries.values())


def test_stylesheet_carries_the_token_stack_and_no_superseded_fonts():
    css = rt.report_css()
    assert "Avenir Next" in css
    assert "Playfair" not in css
    assert "IBM Plex" not in css
    assert "—" not in css          # no em dashes in the shipped sheet
    root = rt.tokens_css()
    assert root.startswith(":root{")
    assert "--t-accent:#fb461f;" in root
    assert "--t-stack:" in root


# --------------------------------------------------------------------------- #
# the mark resolver
# --------------------------------------------------------------------------- #
def test_official_marks_resolve_for_agencies_and_orgs():
    irs = rt.find_mark("Internal Revenue Service", kind="agency")
    assert irs and irs["src"].startswith("data:image/")
    assert rt.find_mark("IRS", kind="agency")
    fcn = rt.find_mark("FCN, INC.", kind="org")
    assert fcn and fcn["src"].startswith("data:image/")
    assert rt.find_mark("THUNDERCAT TECHNOLOGY, LLC", kind="org")


def test_mark_is_never_empty_and_records_gaps():
    gaps: list = []
    official = rt.mark("Internal Revenue Service", kind="agency", gaps=gaps)
    assert "has-img" in official and "<img" in official
    assert gaps == []
    fallback = rt.mark("Bureau of Nowhere", kind="agency", gaps=gaps)
    assert "no-img" in fallback
    assert ">BON<" in fallback           # monogram: first letters, max 3
    assert "no official mark on file" in fallback
    assert "—" not in fallback      # house rule holds in the title text
    assert gaps == ["Bureau of Nowhere"]


def test_monogram_rule_is_first_letters_max_three_uppercase():
    assert rt.monogram_initials("Bureau of the Fiscal Service") == "BOT"
    assert rt.monogram_initials("FCN, INC.") == "FI"
    assert rt.monogram_initials("") == "FED"


def test_claim_refuses_unbound_sentences():
    import pytest
    with pytest.raises(ValueError):
        rt.claim("An unbound market assertion")
    bound = rt.claim("IRS paper renews in September.",
                     aid="2032H519F00718",
                     url_builder=lambda a: f"https://x.example/{a}")
    assert 'href="https://x.example/2032H519F00718"' in bound
    assert "2032H519F00718" in bound


# --------------------------------------------------------------------------- #
# render integration
# --------------------------------------------------------------------------- #
def test_render_ships_style_block_marks_and_gap_receipt():
    import agents.golden_press.validate as validate_mod
    from agents.golden_press.render import render_content_region

    known = _award("KNOWN1", agency="Department of the Treasury",
                   sub="Internal Revenue Service")
    unknown = _award("MYST1", agency="Bureau of Nowhere", sub=None,
                     recipient="MYSTERY PRIME LLC")
    pack = _pack([known, unknown])
    content = render_content_region(pack, prose={})
    # the style block travels with the content; the skeleton stays verbatim
    assert content.count('<style id="lila-aesthetic">') == 1
    assert "--t-accent:#fb461f;" in content
    # band grammar still holds with the style block in place
    assert validate_mod.check_band_grammar(content, pack) == []
    # the IRS card carries the packaged official mark
    assert 'class="seal has-img"' in content
    # the gap receipt names the monogram fallbacks in the method band
    assert "MARKS · OFFICIAL OR MONOGRAM" in content
    assert "Bureau of Nowhere" in content
    assert content.count("<!-- LILA:MARKS-RECEIPT -->") == 0


def test_render_with_all_official_marks_ships_no_gap_receipt():
    from agents.golden_press.render import render_content_region

    pack = _pack([_award("KNOWN1", agency="Department of the Treasury",
                         sub="Internal Revenue Service")])
    content = render_content_region(pack, prose={})
    assert "MARKS · OFFICIAL OR MONOGRAM" not in content
    assert content.count("<!-- LILA:MARKS-RECEIPT -->") == 0
