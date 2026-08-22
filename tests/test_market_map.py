"""The Market Map contract, enforced.

The operator's instruction was to CONTROL for this format, not merely to
produce it once. These tests pin the three laws that make it different from
the band family, and every one of them exists because the band family
drifted in exactly that way.

  L-M1  a blank cell is a defect; a named gap is a work order
  L-M2  a fact appears once; twenty rows for one person is padding
  L-M3  no prose a number could replace, and no invented vocabulary
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import market_map as mm  # noqa: E402
from agents.golden_press.market_map_validate import (  # noqa: E402
    REQUIRED_SECTIONS, validate_market_map)


def _doc(*sections: str) -> str:
    return "<html><body>" + "".join(sections) + "</body></html>"


# a receipt (linked record id) and a coverage claim, which the contract
# requires of the money and opportunity sections
_BASE = ('<p>1 linked record shown. '
         '<a href="https://www.usaspending.gov/award/CONT_AWD_GS35F001">'
         'GS35F001</a></p>')


def _section(sid: str, body: str = _BASE) -> str:
    return mm._wrap(sid, 1, f"This is the {sid} section.", "", body)


def _full(**overrides) -> str:
    parts = []
    for sid in REQUIRED_SECTIONS:
        parts.append(overrides.get(sid, _section(sid)))
    return _doc(*parts)


# ===== §1 the seven sections, in order ==================================== #
def test_a_conformant_document_passes():
    assert validate_market_map(_full())["ok"] is True


def test_a_missing_section_fails():
    parts = [_section(s) for s in REQUIRED_SECTIONS if s != "teaming"]
    out = validate_market_map(_doc(*parts))
    assert out["ok"] is False
    assert any(v["rule"] == "sections_missing" for v in out["violations"])


def test_sections_out_of_order_fail():
    parts = [_section(s) for s in
             ("market", "company") + REQUIRED_SECTIONS[2:]]
    out = validate_market_map(_doc(*parts))
    assert any(v["rule"] == "section_order" for v in out["violations"])


# ===== L-M3 vocabulary ==================================================== #
def test_a_banned_invented_word_fails():
    """The exact defect that shipped an apexanalytix press uncertified."""
    out = validate_market_map(_full(
        market=_section("market", "<p>This is a corridor position.</p>")))
    assert any(v["rule"] == "banned_vocabulary" for v in out["violations"])


@pytest.mark.parametrize("filler", ["leverage", "robust", "delve"])
def test_llm_filler_fails(filler):
    """This family reports; it does not talk."""
    out = validate_market_map(_full(
        company=_section("company", f"<p>We {filler} the data here.</p>")))
    assert any(v["rule"] == "banned_filler" for v in out["violations"])


def test_an_em_dash_fails():
    """The operator's own reference artifact carried one in its section-6
    heading, which would ship a press uncertified."""
    out = validate_market_map(_full(
        contacts=_section("contacts",
                          "<p>People to contact—and what is needed.</p>")))
    assert any(v["rule"] == "em_dash" for v in out["violations"])


# ===== L-M2 never repeat to fill space ==================================== #
def test_a_repeated_sentence_fails():
    line = "<p>The client holds no cited award at this agency today.</p>"
    out = validate_market_map(_full(
        market=_section("market", line + "<p>1 linked record shown.</p>"),
        teaming=_section("teaming", line + "<p>1 linked record shown.</p>")))
    assert any(v["rule"] == "repeated_sentence" for v in out["violations"])


def test_an_identical_row_fails():
    """THE TWENTY-KYLE-BARNER DEFECT. One contact appeared twenty times
    because twenty specs named the same agency."""
    row = "<tr><td>Kyle Barner</td><td>DLA</td><td>kyle@dla.mil</td></tr>"
    table = f"<table><tbody>{row}{row}</tbody></table>" + _BASE
    out = validate_market_map(_full(contacts=_section("contacts", table)))
    assert any(v["rule"] == "repeated_row" for v in out["violations"])


def test_a_shared_category_label_is_not_padding():
    """NEGATIVE, and the false positive I shipped first. Two rows sharing a
    motion label are how a table groups; only an IDENTICAL row is padding.
    A validator that cries wolf trains an operator to ignore it."""
    table = ("<table><tbody>"
             "<tr><td>Status check</td><td>Dana Reyes</td><td>a@irs.gov</td></tr>"
             "<tr><td>Status check</td><td>Sam Cole</td><td>b@irs.gov</td></tr>"
             "</tbody></table>" + _BASE)
    out = validate_market_map(_full(contacts=_section("contacts", table)))
    assert not any(v["rule"] == "repeated_row" for v in out["violations"])


# ===== L-M1 say what we do not have ======================================= #
def test_a_blank_cell_fails():
    table = ("<table><tbody><tr><td>FCN, Inc.</td><td></td></tr></tbody>"
             "</table>" + _BASE)
    out = validate_market_map(_full(teaming=_section("teaming", table)))
    assert any(v["rule"] == "blank_cell" for v in out["violations"])


def test_a_named_gap_passes_where_a_blank_would_fail():
    cell = mm._cell("", "Enrichment needed")
    table = (f"<table><tbody><tr><td>FCN, Inc.</td><td>{cell}</td></tr>"
             f"</tbody></table>" + _BASE)
    out = validate_market_map(_full(teaming=_section("teaming", table)))
    assert out["ok"] is True
    assert out["receipts"]["stated_gaps"] >= 1


def test_every_gap_phrase_is_from_the_sanctioned_list():
    """A free-text gap would drift back into prose. The list is the API."""
    for phrase in mm.GAP_PHRASES:
        assert mm._cell("", phrase).count(phrase) == 1
    assert "Not in current research" in mm.GAP_PHRASES


def test_a_section_that_states_no_coverage_fails():
    out = validate_market_map(_full(
        competition=mm._wrap("competition", 3, "Rivals.", "", "<p>x</p>")))
    assert any(v["rule"] == "coverage_unstated" for v in out["violations"])


# ===== receipts and client hygiene ======================================== #
def test_a_money_section_with_no_record_id_fails():
    out = validate_market_map(_full(
        market=mm._wrap("market", 2, "Money.", "",
                        "<p>Total is $5,000,000.00. 1 linked record "
                        "shown.</p>")))
    assert any(v["rule"] == "missing_receipt" for v in out["violations"])


def test_a_script_in_the_client_artifact_fails():
    out = validate_market_map(_full() + "<script>x()</script>")
    assert any(v["rule"] == "script_in_client_artifact"
               for v in out["violations"])


# ===== L-M3 structurally: no model call can reach this family ============= #
def test_the_renderer_cannot_call_a_model():
    """Not a rule against prose, an absence of any mechanism for it."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(mm.__file__).read_text(encoding="utf-8"))
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    # IMPORTS, not words: the docstring legitimately discusses prose, and a
    # substring scan that flags its own explanation is not a check.
    for forbidden in ("maxplan_cli", "openai", "anthropic", "compose"):
        assert not any(forbidden in name for name in imported), (
            f"{forbidden} reachable from the Market Map renderer")


def test_the_market_total_reconciles_to_its_own_segments():
    """The $106bn defect: category_spend rows are PRE-CAP, so summing them
    and calling the result "cited" states a number no record supports."""
    from types import SimpleNamespace as NS
    pack = NS(client_name="Acme", records=[
        NS(lane="L2_entity_award", recipient="ACME INC", agency="IRS",
           obligated_dollars=100.0, record_id="A1", url="", ceiling_dollars=0),
        NS(lane="L4_forecast", recipient="", agency="VA",
           obligated_dollars=0.0, record_id="F1", url="", ceiling_dollars=0)],
        research={"entities": {"client": ["ACME INC"]}})
    segments = mm._cited_segments(pack)
    assert sum(c for _, _, c in segments) == len(pack.records)
    assert sum(d for _, d, _ in segments) == 100.0
