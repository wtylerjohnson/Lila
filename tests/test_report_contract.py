"""REPORT_CONTRACT enforcement (Task 2, 2026-08-03).

docs/REPORT_CONTRACT.md is the ONE band-list source: the loader parses §1,
validate/compose/render derive from it, and the validator enforces the §3
mechanical invariants plus §2 required fields. Offline; no network, no LLM.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.contract import (  # noqa: E402
    ContractError,
    banned_vocabulary,
    contract_section_ids,
    load_contract_bands,
)


def test_the_band_table_parses_in_contract_order():
    # AMENDMENT v1.3 (2026-08-05): Band 06 competitive; events and method
    # renumber to 07/08 by table position.
    # AMENDMENT v1.4 (2026-08-05): Band 09 targeting, the action layer,
    # after 08 method and before the footer. Nothing below it renumbers.
    bands = load_contract_bands()
    assert [b.band_id for b in bands] == [
        "hero", "decisions", "forward", "clocks", "paper", "agencies",
        "competitive", "events", "method", "targeting", "footer"]
    assert [b.number for b in bands] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, None]
    assert all(b.required for b in bands)
    assert contract_section_ids() == (
        "decisions", "forward", "clocks", "paper", "agencies", "competitive",
        "events", "method", "targeting")


def test_a_stale_printed_number_fails_loudly(tmp_path, monkeypatch):
    """The contract's own no-stale-numerals law applies to its table."""
    doctored = (
        "## 1. BAND SEQUENCE\n\n"
        "| # | id | name | required | zero-state |\n"
        "|---|----|------|----------|-----------|\n"
        "| 00 | hero | Hero | always | n/a |\n"
        "| 02 | decisions | Account decisions | always | zero |\n"
        "| 02 | forward | Forward lane | always | zero |\n"
        "\n## 2. X\n")
    path = tmp_path / "contract.md"
    path.write_text(doctored, encoding="utf-8")
    monkeypatch.setenv("LILA_REPORT_CONTRACT_PATH", str(path))
    with pytest.raises(ContractError):
        load_contract_bands()


def test_l7_banned_vocabulary_parses_from_the_contract():
    terms = banned_vocabulary()
    assert "corridor" in terms
    assert "research clock" in terms
    assert "360° assessment" in terms
    assert "preliminary match" in terms


def test_doctrine_speaks_for_every_contract_band():
    from agents.golden_press.doctrine import BY_ID
    for band_id in contract_section_ids():
        assert band_id in BY_ID
        assert BY_ID[band_id].heading


def test_client_export_is_pure_and_studio_build_is_not():
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import (
        check_client_purity,
        client_export,
    )
    from agents.golden_press.records import EvidencePack, GoldenRecord

    pack = EvidencePack(
        client_name="Acme Networks", generated_at="2026-08-01T12:00:00Z",
        records=[GoldenRecord(
            record_id="CLIENT1", lane="L2_entity_award",
            title="Enterprise agreement",
            agency="Department of the Treasury",
            sub_agency="Internal Revenue Service", recipient="FCN, INC.",
            entity_hits=["AcmeFlow"], obligated_dollars=1_000_000.0,
            period_end="2026-12-31",
            url="https://www.usaspending.gov/award/CONT_AWD_CLIENT1")],
        research={"entities": {"product": ["AcmeFlow"], "competitor": []}})
    content = render_content_region(pack, prose={})
    assert "data-edit-id" in content          # the studio build keeps its tools
    exported = client_export(content)
    assert check_client_purity(exported) == []
    assert 'data-edit-id="' not in exported      # attributes, not css
    assert 'data-studio-unit-id="' not in exported
    # the evidence itself survives the export untouched
    assert "CLIENT1" in exported
    assert 'href="https://www.usaspending.gov/award/CONT_AWD_CLIENT1"' \
        in exported


def test_banned_vocabulary_check_is_word_bounded():
    from agents.golden_press.validate import check_banned_vocabulary
    dirty = "<p>The corridor strategy rides a research clock.</p>"
    rules = [v["rule"] for v in check_banned_vocabulary(dirty)]
    assert rules.count("banned_vocabulary") == 2
    # word boundary: 'corridors' is caught, 'uncorridored' nonsense is not
    assert check_banned_vocabulary("<p>encorridorment</p>") == []


def test_prose_carrying_banned_vocabulary_falls_back():
    from agents.golden_press.compose import _prose_usable
    assert _prose_usable(
        "This corridor anchors the whole federal estate today.") is None
    assert _prose_usable(
        "This buying account anchors the whole federal estate today.")


def test_skeleton_nav_rebuilds_from_the_contract():
    from agents.golden_press.skeleton import add_band_nav
    skeleton = ('<nav class="sb-nav" aria-label="Report sections">'
                '<a href="#forecast">360° assessment</a>'
                '<a href="#evidence">Sources</a></nav>')
    rebuilt = add_band_nav(skeleton)
    assert "360°" not in rebuilt
    assert '<a href="#decisions">Decisions</a>' in rebuilt
    assert '<a href="#method">Method</a>' in rebuilt
    assert "#forecast" not in rebuilt


def test_pack_carried_dollar_text_is_provenanced():
    """A per-user price inside an award description ('PPU: $6,750.00') is
    the record's own text; rendering it verbatim is evidence, not a minted
    figure (the FiscalNote press regression, 2026-08-03)."""
    from agents.golden_press.validate import check_dollars
    from agents.golden_press.records import EvidencePack, GoldenRecord
    pack = EvidencePack(
        client_name="FiscalNote", generated_at="2026-08-03T12:00:00Z",
        records=[GoldenRecord(
            record_id="F4700X", lane="L2_entity_award",
            title="SPOC LEGISLATIVE SUBSCRIPTION",
            description="QTY: 1 USER PPU: $6,750.00 STARCOM LEGISLATIVE",
            agency="Department of the Air Force",
            obligated_dollars=166_900.0,
            url="https://www.usaspending.gov/award/CONT_AWD_F4700X")])
    ok = "<p>QTY: 1 USER PPU: $6,750.00 STARCOM</p>"
    assert check_dollars(ok, pack) == []
    minted = "<p>The estate totals $9,999,999 today.</p>"
    assert len(check_dollars(minted, pack)) == 1


def test_decision_cards_and_forward_rows_render_distinct_fragments():
    """Deterministic templates must never repeat a 48+ char fragment across
    cards or rows (the FiscalNote press regression, 2026-08-03)."""
    from agents.golden_press.decision_rules import build_decisions
    from agents.golden_press.render import render_content_region
    from agents.golden_press.validate import check_repeated_prose
    from agents.golden_press.records import EvidencePack, GoldenRecord

    def _award(rid, sub, hits, end):
        return GoldenRecord(
            record_id=rid, lane="L2_entity_award",
            title=f"Enterprise agreement {rid.lower()}",
            agency="Department of Defense", sub_agency=sub,
            recipient=f"PRIME {rid} LLC", entity_hits=list(hits),
            obligated_dollars=1_000_000.0, period_end=end,
            url=f"https://www.usaspending.gov/award/CONT_AWD_{rid}")

    records = [
        _award("C1", "Department of the Navy", ["AcmeFlow"], "2026-12-31"),
        _award("R1X", "Department of the Navy", ["RivalSuite"], "2026-11-30"),
        _award("C2", "Department of the Army", ["AcmeFlow"], "2026-10-31"),
        _award("R2X", "Department of the Army", ["RivalSuite"], "2026-09-30"),
        _award("C3", "Defense Information Systems Agency", ["AcmeFlow"],
               "2027-01-31"),
        _award("R3X", "Defense Information Systems Agency", ["RivalSuite"],
               "2027-02-28"),
    ]
    pack = EvidencePack(
        client_name="Acme Networks", generated_at="2026-08-01T12:00:00Z",
        records=records,
        research={"entities": {"product": ["AcmeFlow"],
                               "competitor": ["RivalSuite"]}})
    pack.decisions = build_decisions(pack)
    content = render_content_region(pack, prose={})
    assert check_repeated_prose(content) == []
