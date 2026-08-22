"""L13: scoreboard population honesty. Every figure's count, named examples,
and 'and X more' come from ONE population; a company appears under exactly one
label (incumbent-in-buyer-accounts > product competitor > lane awardee);
counts()["competitors"] stays product-gated for client copy.

Offline — fixture searches + in-memory profile, no subprocesses, no LLM.
"""

from __future__ import annotations

import copy
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.document import (  # noqa: E402
    build_document, company_matches, normalize_company, resolve_count_tokens,
)
from agents.reports.lint import lint_scoreboard_populations  # noqa: E402
from agents.reports.views import _pop_figure, render_scoreboard  # noqa: E402
from tests.test_assessment_document import _searches  # noqa: E402
from tools.capability import CapabilityTerms, ClientProfile  # noqa: E402

OEM_PROFILE = ClientProfile(
    client_name="Testco",
    capability_terms=CapabilityTerms(core=["network visibility"]),
    named_competitors_and_incumbents=[
        "Testco", "Gigamon", "Corelight", "Riverbed", "SolarWinds"],
    naics_boundary=["334310"],
)


def _netscout_shape(n_lane=33, buyers=None):
    """The NETSCOUT case: profile names OEMs, landscape holds n_lane lane
    primes (no OEM among them), buyer map holds OEM product rows."""
    s = copy.deepcopy(_searches(3))
    lanes = s["results"]["usaspending.gov"][0]
    lanes["summary"]["top_incumbents"] = [f"Lane Prime {i:02d}" for i in range(n_lane)]
    lanes["awards"] = []
    s["results"]["subawards"] = []
    s["results"]["incumbent_buyer_map"] = {"buyers": buyers if buyers is not None else [
        {"buyer": "CBP", "agency": "DHS", "products": ["Gigamon Inc.", "Testco"]},
        {"buyer": "USCIS", "agency": "DHS", "products": ["Corelight", "Gigamon Inc."]},
        {"buyer": "TSA", "agency": "DHS", "products": ["Riverbed"]},
    ]}
    return s


def _tagged_doc(monkeypatch, **kw):
    monkeypatch.setattr("tools.capability.load_profile",
                        lambda name: OEM_PROFILE)
    return build_document("Testco", searches=_netscout_shape(**kw), qualify=None)


def _pop_blocks(html):
    return {m.group(1): m.group(0) for m in re.finditer(
        r'<div class="vb-comp" data-pop="(\w+)" data-pop-count="\d+">.*?</div>',
        html, re.S)}


# ---- 1. the NETSCOUT case renders three honest figures ----------------------
def test_netscout_case_three_populations_no_cross_pairing(monkeypatch):
    doc = _tagged_doc(monkeypatch)
    n_lane = len(doc.competitive.competitors)
    assert n_lane >= 30                      # the 33-lane-prime shape held
    assert doc.counts()["competitors"] == 0  # product-gated, none tagged

    html = render_scoreboard(doc, "internal")
    blocks = _pop_blocks(html)
    assert 'data-pop="product" data-pop-count="0"' in html
    assert f'data-pop="lane" data-pop-count="{n_lane}"' in html
    assert 'data-pop="incumbent" data-pop-count="3"' in html
    assert "0 product competitors identified" in blocks["product"]
    assert f"{n_lane} repeat lane awardees" in blocks["lane"]
    assert "3 product incumbents in buyer accounts" in blocks["incumbent"]
    # incumbents carry company and agency; the client itself is excluded
    assert "Gigamon (CBP, USCIS)" in blocks["incumbent"]
    assert "Testco" not in blocks["incumbent"]
    # no line pairs a count from one population with names from another:
    # the product figure (0) names nobody; lane names live in the lane block
    assert blocks["product"].count('data-pop-name="1"') == 0
    assert "Lane Prime" not in blocks["product"]
    assert "Lane Prime" not in blocks["incumbent"]
    assert "Gigamon" not in blocks["lane"]
    lint = lint_scoreboard_populations(html)
    assert lint.ok, [v.detail for v in lint.violations]


# ---- 2. 'and X more' can never go negative ----------------------------------
def test_and_x_more_never_negative():
    # populations of size 0, 1, 3, 4: remainder is len - shown, never negative
    for n in (0, 1, 3, 4, 40):
        entries = [(f"Co {i}", f"Co {i}") for i in range(n)]
        html = _pop_figure("lane", entries, "repeat lane awardees", "none")
        m = re.search(r"and (-?\d+) more", html)
        if m:
            assert int(m.group(1)) > 0
        assert lint_scoreboard_populations(
            f'<div class="vb-comp" data-pop="lane" data-pop-count="{n}">'
            + html.split(">", 1)[1]).ok
    # the lint catches a hand-built cross-population line (the old defect:
    # count 0 from the product population, names from the lane population)
    bad = ('<div class="vb-comp" data-pop="product" data-pop-count="0">'
           '<b>0 product competitors identified.</b> '
           '<span data-pop-name="1">BOOZ ALLEN</span>, '
           '<span data-pop-name="1">SAIC</span> and -3 more</div>')
    res = lint_scoreboard_populations(bad)
    assert not res.ok
    assert any("negative" in v.detail or "!=" in v.detail
               for v in res.violations)


# ---- 3. client copy regression pin: counts() stays product-gated ------------
def test_counts_and_count_token_stay_product_gated(monkeypatch):
    doc = _tagged_doc(monkeypatch)
    assert doc.counts()["competitors"] == 0
    assert resolve_count_tokens("{{COUNT:competitors}} competitors",
                                doc.counts()) == "0 competitors"
    assert resolve_count_tokens("{{COUNT_WORD:competitors}}",
                                doc.counts()) == "zero"


# ---- 4. name matching: normalized, word-boundary ----------------------------
def test_company_name_matching_rules():
    assert company_matches("Gigamon Inc.", "Gigamon")
    assert company_matches("SolarWinds Corporation", "SolarWinds")
    assert company_matches("GIGAMON INC.", "gigamon")          # case-insensitive
    assert company_matches("Booz Allen Hamilton Inc", "Booz Allen")
    assert not company_matches("River Edge Systems", "Riverbed")
    assert not company_matches("", "Gigamon")
    assert normalize_company("Gigamon, Inc.") == normalize_company("GIGAMON")
    assert normalize_company("ThunderCat Technologies LLC") == "thundercat"


# ---- 5. dedupe: one company, one label, incumbent wins ----------------------
def test_dedupe_incumbent_label_wins(tmp_path, monkeypatch):
    s = _netscout_shape()
    # Gigamon shows up as a lane awardee too: present in BOTH populations
    s["results"]["usaspending.gov"][0]["summary"]["top_incumbents"].append(
        "Gigamon Inc.")
    monkeypatch.setattr("tools.capability.load_profile",
                        lambda name: OEM_PROFILE)
    doc = build_document("Testco", searches=s, qualify=None)
    gig = [c for c in doc.competitive.competitors
           if company_matches(c.name, "Gigamon")]
    assert gig and gig[0].product           # the shared matcher tagged it

    html = render_scoreboard(doc, "internal")
    blocks = _pop_blocks(html)
    # exactly one appearance, under the incumbent label
    assert html.count("Gigamon") == blocks["incumbent"].count("Gigamon")
    assert "Gigamon" in blocks["incumbent"]
    # totals reflect the deduped assignment: product excludes the reassigned
    # company even though the tagger marked it
    assert 'data-pop="product" data-pop-count="0"' in html
    assert lint_scoreboard_populations(html).ok

    # the drill-down endpoint labels the same assignment
    flask = pytest.importorskip("flask")  # noqa: F841
    import json as _json
    import ui.server as srv
    from tests.test_ui import _mk_client
    root = str(tmp_path)
    monkeypatch.setattr(srv, "REVIEW_DIR", _mk_client(root, slug="testco",
                                                      name="Testco"))
    cleaned = os.path.join(root, "data", "cleaned")
    os.makedirs(cleaned, exist_ok=True)
    monkeypatch.setattr(srv, "CLEANED_DIR", cleaned)
    with open(os.path.join(cleaned, "searches_testco.json"), "w") as f:
        _json.dump(s, f)
    rows = (srv.app.test_client().get("/api/client/testco/competitors")
            .get_json()["competitors"])
    by_pop = {r["name"]: r["population"] for r in rows}
    assert by_pop["Gigamon Inc."] == "product incumbent in buyer accounts"
    assert by_pop["Lane Prime 00"] == "lane awardee"
