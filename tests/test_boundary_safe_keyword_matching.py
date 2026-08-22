"""Regression tests for boundary-safe source keyword screens."""

from __future__ import annotations

import pytest

import tools.api.darpa_opportunities as darpa
import tools.api.dod_budget_exhibits as dod_budget
import tools.api.foreign_assistance as foreign_assistance
import tools.api.gao_legal as gao_legal
import tools.api.reginfo_unified_agenda as reginfo
from tools.api.base import SourceQuery
from tools.api.dod_contracts import _matched_terms as dod_matched_terms
from tools.text_match import matching_phrases, phrase_matches


def test_phrase_matching_rejects_embedded_agni_and_disa_tokens() -> None:
    assert not phrase_matches("magnitude monitoring", "AGNI")
    assert not phrase_matches("Disaster Assistance program", "DISA")
    assert matching_phrases("AGNI and DISA network modernization", ["AGNI", "DISA"]) == [
        "AGNI",
        "DISA",
    ]


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Command & Control platform", "command & control"),
        ("Command and Control platform", "command & control"),
        ("C3 AI mission platform", "C3.ai"),
        ("A.I. assurance", "AI"),
        ("C++ compiler modernization", "C++"),
        ("zero-trust/network fabric", "zero trust network fabric"),
    ],
)
def test_phrase_matching_normalizes_meaningful_punctuation(
    text: str, term: str
) -> None:
    assert phrase_matches(text, term)


def test_c_plus_plus_never_collapses_to_unrelated_c_token() -> None:
    assert not phrase_matches("C language compiler", "C++")


def test_gao_disa_screen_does_not_match_disaster_assistance(monkeypatch) -> None:
    feed = """<?xml version="1.0"?>
    <rss><channel>
      <item><title>Disaster Assistance Oversight</title>
        <link>https://gao.example/disaster</link>
        <description>Emergency recovery review</description></item>
      <item><title>DISA Network Modernization</title>
        <link>https://gao.example/disa</link>
        <description>Defense Information Systems Agency review</description></item>
    </channel></rss>"""
    monkeypatch.setattr(gao_legal, "get_text", lambda *_args, **_kwargs: feed)

    result = gao_legal.GaoLegalSource().enrich(SourceQuery(keywords=["DISA"]))

    assert [row["url"] for row in result["items"]] == ["https://gao.example/disa"]
    assert result["items"][0]["matched"] == ["DISA"]


def test_dod_contract_screen_rejects_agni_inside_magnitude() -> None:
    assert dod_matched_terms(
        {"title": "Magnitude upgrade", "body": "sensor work"},
        ["AGNI"],
    ) == []
    assert dod_matched_terms(
        {"title": "AGNI upgrade", "body": "sensor work"},
        ["AGNI"],
    ) == ["AGNI"]


@pytest.mark.parametrize(
    "matcher",
    [
        dod_budget._matches,
        foreign_assistance._matches,
        reginfo._matches,
        darpa._matches,
    ],
)
def test_program_keyword_screens_reject_embedded_agni(matcher) -> None:
    query = SourceQuery(keywords=["AGNI"])
    assert not matcher({"title": "Magnitude modernization"}, query)
    assert matcher({"title": "AGNI modernization"}, query)


# The HHS leg of this contract died with the probe path (2026-08-18): the
# census never narrows at the wire, so hhs_sbcx has no keyword screen to
# hold to boundary discipline. Downstream matching rides match_forecasts,
# which the parametrized matchers above already cover.
