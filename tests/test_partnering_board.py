"""PartneringBoard composition + rendering: plays only where blockers fire,
counts through counts(), gated sales shape with verifier lines, internal
worksheets and profile warnings, FOA no-padding. Offline."""

from __future__ import annotations

import copy
import json
import os
import sys
from datetime import date
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.document import build_document  # noqa: E402
from agents.reports.lint import (  # noqa: E402
    lint_client_terminology, lint_counts, lint_emdash, lint_whitelabel,
)
from agents.reports.views import (  # noqa: E402
    gate_for_sales, render_assessment, render_dossier_html,
    render_sales_teaser_body, render_scoreboard,
)
from tests.test_assessment_document import _searches  # noqa: E402

AS_OF = date(2026, 7, 7)


def _partnering_searches():
    """P2 gets a total-small set-aside + the lane gets subaward artifacts —
    with an other_than_small attestation the ELIGIBILITY blocker fires on P2
    and only P2."""
    s = copy.deepcopy(_searches(3))
    for n in s["results"]["sam.gov"]:
        if n["source_id"] == "P2":
            n["set_aside"] = "Total Small Business Set-Aside (FAR 19.5)"
    s["results"]["subawards"] = {
        "primes": [{"name": "GDIT", "subaward_count": 3, "total": 60_000_000.0}],
        "edges": {"334310": [
            {"subaward_id": "SA1", "prime": "AVI-SPL", "sub": "TinyTech",
             "amount": 250_000.0, "prime_award_id": "W91ABC24C0001",
             "date": "2025-03-01", "source": "https://api.usaspending.gov"},
        ]},
        "demand": {"threshold_usd": 900_000, "rows_by_naics": {"334310": [
            {"award_id": "D1", "recipient": "MegaPrime Corp",
             "amount": 2_000_000.0, "awarding_agency": "Department of Defense",
             "start_date": "2025-02-01",
             "size_basis": "recipient_type_names=other_than_small_business (filter)",
             "source": "https://api.usaspending.gov"}]}},
        "small_awards": {"334310": [
            {"award_id": "S1", "recipient": "SmallCo LLC", "amount": 300_000.0,
             "awarding_agency": "Department of Defense", "start_date": "2025-04-01",
             "size_basis": "recipient_type_names=small_business (filter)",
             "source": "https://api.usaspending.gov"}]},
    }
    return s


def _profile(tmp_path, monkeypatch, body):
    monkeypatch.setenv("LILA_REVIEW_DIR", str(tmp_path))
    (tmp_path / "testco.partnering.json").write_text(json.dumps(body))


def _doc(tmp_path, monkeypatch, with_profile=True):
    if with_profile:
        _profile(tmp_path, monkeypatch, {
            "size_status_by_naics": {"334310": "other_than_small",
                                     "541512": "other_than_small"},
            "certifications": [],
            "vehicles_held": [],
            "award_band": {"floor_usd": 50_000, "ceiling_usd": 1_500_000}})
    else:
        monkeypatch.setenv("LILA_REVIEW_DIR", str(tmp_path))
    return build_document("Testco", searches=_partnering_searches(),
                          qualify=None, as_of=AS_OF)


class _PursuitCardScan(HTMLParser):
    """Collect teaming/candidate structure under each pursuit-card wrapper."""

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active_rank: int | None = None
        self.stack: list[tuple[str, int | None]] = []
        self.cards: dict[int, dict] = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        prior = self.active_rank
        rank = attrs.get("data-pursuit-rank")
        if tag == "div" and rank and rank.isdigit():
            self.active_rank = int(rank)
            self.cards.setdefault(self.active_rank, {
                "text": [], "teaming": 0, "candidates": 0, "locked": 0,
            })
        if self.active_rank is not None:
            card = self.cards[self.active_rank]
            if attrs.get("data-entity") == "teaming_play":
                card["teaming"] += 1
            if attrs.get("data-entity") == "partner_candidate":
                card["candidates"] += 1
            if attrs.get("data-locked") == "1":
                card["locked"] += 1
        if tag not in self._VOID:
            self.stack.append((tag, prior))

    def handle_endtag(self, tag):
        while self.stack:
            open_tag, prior = self.stack.pop()
            if open_tag == tag:
                self.active_rank = prior
                break

    def handle_data(self, data):
        if self.active_rank is not None:
            text = " ".join(data.split())
            if text:
                self.cards[self.active_rank]["text"].append(text)


def _pursuit_cards(html: str) -> dict[int, dict]:
    scan = _PursuitCardScan()
    scan.feed(html)
    for card in scan.cards.values():
        card["text"] = " ".join(card["text"])
    return scan.cards


def test_plays_exist_only_where_blockers_fire_and_count_flows(tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    assert doc.counts()["teaming_plays"] == 1          # P2 only
    play = doc.partnering.plays[0]
    assert play.source_id == "P2"
    assert play.directions == ["SUB_TO_PRIME"]
    assert any("52.219-14" in f for f in play.review_flags)
    names = [c.name for c in play.candidates]
    assert "AVI-SPL" in names and "MegaPrime Corp" in names
    assert all(c.evidence for c in play.candidates)    # evidence invariant
    avi = next(c for c in play.candidates if c.name == "AVI-SPL")
    assert avi.dual_role                               # also a competitor
    assert doc.partnering.profile_unset == []


def test_missing_profile_degrades_with_gaps_and_no_plays(tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch, with_profile=False)
    assert doc.counts()["teaming_plays"] == 0
    assert any("unattested" in g for g in doc.gaps)
    assert any("eligibility blocker N/A" in g for g in doc.gaps)
    internal = render_assessment(doc, "internal")
    assert "PROFILE" in internal and "partnering profile" in internal
    # FOA no-padding: nothing teaming-shaped renders anywhere
    client = render_assessment(doc, "client")
    assert "Teaming play" not in client


def test_internal_renders_full_board_client_renders_the_play(tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    internal = render_assessment(doc, "internal")
    assert "Teaming play · pursuit #" in internal
    assert "FIRED" in internal                          # blockers table
    assert "Partner fit worksheet" in internal
    assert "DUAL ROLE" in internal
    assert "Evidence rows" in internal

    client = render_assessment(doc, "client")
    # one pursue play (P2) in the pursuit section + one monitor teaming-watch
    # play (M1 adjacency); no padding beyond what the data supports
    assert client.count('data-entity="teaming_play"') == doc.counts()["teaming_plays"] \
        + doc.counts()["teaming_watch"] == 2
    assert "Teaming play · pursuit #" in client              # the pursue play
    assert "Teaming Watch</h2>" in client                    # the monitor section
    assert "Direction:" in client and "subcontract to a prime" in client
    assert "52.219-14" in client                        # flag rides to the FOA
    for view, html in (("client", client), ("internal", internal)):
        assert lint_counts(html).ok, view
    assert lint_whitelabel(client).ok            # teaming copy is white-label


def test_partner_path_is_inside_matching_pursuit_card_in_all_three_views(
        tmp_path, monkeypatch):
    """T3: one opportunity-specific play renders on its pursuit card.

    Client and internal carry full evidence-graded identities. Sales carries
    direction plus a verifier-bearing lock, with no candidate identity or
    candidate structural row left in the rendered model.
    """
    doc = _doc(tmp_path, monkeypatch)
    play = next(p for p in doc.partnering.plays if p.kind == "pursue")
    assert play.rank == 2 and play.source_id == "P2"

    rendered = {
        view: render_assessment(doc, view)
        for view in ("client", "internal", "sales")
    }
    cards = {view: _pursuit_cards(html)
             for view, html in rendered.items()}

    for view in ("client", "internal", "sales"):
        assert cards[view][play.rank]["teaming"] == 1
        assert "Direction:" in cards[view][play.rank]["text"]
        assert "subcontract to a prime" in cards[view][play.rank]["text"]
        assert all(card["teaming"] == 0 for rank, card in cards[view].items()
                   if rank != play.rank)
        assert lint_counts(rendered[view]).ok
        assert lint_whitelabel(rendered[view]).ok
        assert lint_emdash(rendered[view]).ok
        assert lint_client_terminology(rendered[view]).ok

    for view, limit in (("client", 3), ("internal", 8)):
        shown = play.candidates[:limit]
        assert cards[view][play.rank]["candidates"] == len(shown)
        for candidate in shown:
            assert candidate.name in cards[view][play.rank]["text"]
            assert candidate.fit is not None
            assert f"partner fit {candidate.fit.score:g}/4" in rendered[view]

    sales_card = cards["sales"][play.rank]
    assert sales_card["candidates"] == 0 and sales_card["locked"] >= 2
    assert "Named evidence-graded candidates" in sales_card["text"]
    assert "Verified against USAspending" in sales_card["text"]
    assert all(candidate.name not in rendered["sales"]
               for candidate in play.candidates)
    client_visible = rendered["client"].lower()
    banned = (
        "unverified", "not verified", "did not verify", "no record",
        "not capability-verified", "pending verification", "zero matched",
        "could not confirm",
    )
    assert not [phrase for phrase in banned if phrase in client_visible]


def test_standalone_dossier_keeps_its_opportunity_specific_partner_path(
        tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    play = next(p for p in doc.partnering.plays if p.kind == "pursue")
    pursuit = next(p for p in doc.board.pursuits if p.rank == play.rank)
    # The fixture's teaming play is on P2, while only P1 arrives with depth.
    # Supply deterministic depth so the standalone rank-2 export is available.
    pursuit.dossier = {
        "scope_summary": "Courtroom audiovisual refresh requirement.",
        "vehicle": "full and open",
        "next_action": "Validate the teaming workshare.",
        "win_themes": [],
        "red_flags": [],
    }

    html = render_dossier_html(doc, play.rank)

    assert html is not None
    card = _pursuit_cards(html)[play.rank]
    assert card["teaming"] == 1
    assert card["candidates"] == min(8, len(play.candidates))
    assert "subcontract to a prime" in card["text"]
    assert all(candidate.name in card["text"]
               for candidate in play.candidates[:8])


def test_sales_gating_locks_names_keeps_shape_and_recomputes_counts(tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    doc.board.pursuits[0].fit_trace = {
        "matched_terms": ["SECRET_FIT_TERM"],
        "screen_inference": "SECRET_SCREEN_INFERENCE",
    }
    gated = gate_for_sales(doc)
    assert gated.counts()["teaming_plays"] == 1        # recomputed, truthful
    g = gated.partnering.plays[0]
    assert g.candidates == [] and g.blockers == [] and g.review_flags == []
    assert g.locked and g.locked.verifiers["candidates"]
    dump = gated.model_dump_json().casefold()
    candidate_names = {
        candidate.name
        for play in doc.partnering.plays
        for candidate in play.candidates
    }
    for secret in (*candidate_names, "W91ABC24C0001"):
        assert secret.casefold() not in dump
    assert all(pursuit.fit_trace is None for pursuit in gated.board.pursuits)
    assert "secret_fit_term" not in dump
    assert "secret_screen_inference" not in dump
    # the play carries the SAME masked id as its pursuit — never the raw one
    p2 = next(p for p in gated.board.pursuits if p.rank == g.rank)
    assert g.source_id == p2.source_id and "XXXX" in g.source_id

    sales = render_assessment(doc, "sales")
    assert "Teaming path · pursuit #" in sales
    assert "Verified against USAspending" in sales
    assert "aggregate dollars" in sales
    client = render_assessment(doc, "client")
    internal = render_assessment(doc, "internal")
    sales_folded = sales.casefold()
    for secret in candidate_names:
        assert secret.casefold() not in sales_folded
    for candidate in next(p for p in doc.partnering.plays
                          if p.kind == "pursue").candidates[:3]:
        assert candidate.name in client and candidate.name in internal
    assert lint_counts(sales).ok


def test_sales_gate_drops_candidate_news_rows_and_backfills_verbatim(
        tmp_path, monkeypatch):
    """2026-07-12: cited news is atomic at the partner-identity boundary.

    A candidate-bearing headline or URL leaves the gated model as one row.
    Safe rows backfill the three sales slots without token substitution, while
    client and internal views retain the original third-party citations.
    """
    doc = _doc(tmp_path, monkeypatch)
    blocked_title = {
        "title": "AVI-SPL protests $90M DHS award",
        "url": "https://www.defensenews.example/dhs-award",
        "published": "2026-07-11",
        "source": "Defense News",
    }
    blocked_url = {
        "title": "Integrator protests the DHS award",
        "url": "https://www.govcon.example/avi-spl-protest",
        "published": "2026-07-10",
        "source": "GovCon Wire",
    }
    blocked_source = {
        "title": "DHS award activity enters its next phase",
        "url": "https://www.market.example/dhs-award-activity",
        "published": "2026-07-10",
        "source": "AVI-SPL",
    }
    safe_news = [
        {
            "title": "DHS advances its audiovisual modernization plan",
            "url": "https://www.news.example/dhs-av-plan",
            "published": "2026-07-09",
            "source": "Federal News",
        },
        {
            "title": "Courts publish a courtroom technology roadmap",
            "url": "https://www.news.example/courts-roadmap",
            "published": "2026-07-08",
            "source": "Court News",
        },
        {
            "title": "Defense agencies expand secure collaboration",
            "url": "https://www.news.example/secure-collaboration",
            "published": "2026-07-07",
            "source": "Defense Daily",
        },
    ]
    doc.market.news = [blocked_title, safe_news[0], blocked_url,
                       blocked_source, safe_news[1], safe_news[2]]

    gated = gate_for_sales(doc)

    assert gated.market.news == safe_news
    assert gated.counts()["news_items"] == 3
    gated_dump = gated.model_dump_json().casefold()
    assert "avi-spl" not in gated_dump
    assert all("Included in the full engagement" not in item.get(field, "")
               for item in gated.market.news for field in ("title", "url"))

    sales_assessment = render_assessment(doc, "sales")
    sales_body = render_sales_teaser_body(doc)
    sales_scoreboard = render_scoreboard(doc, "sales")
    for rendered in (sales_assessment, sales_body, sales_scoreboard):
        assert "avi-spl" not in rendered.casefold()
        for item in safe_news:
            assert item["title"] in rendered
    for rendered in (sales_assessment, sales_body):
        for item in safe_news:
            assert item["url"] in rendered

    client = render_assessment(doc, "client")
    internal = render_assessment(doc, "internal")
    for rendered in (client, internal):
        for item in (blocked_title, blocked_url, blocked_source):
            assert item["title"] in rendered
            assert item["url"] in rendered


def test_banner_carries_the_partnering_tile_both_views(tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    internal = render_scoreboard(doc, "internal")
    assert "teaming play" in internal and "pursuit board" in internal
    assert 'data-counts-verified="1">1</span> teaming play' in internal
    sales = render_scoreboard(doc, "sales")
    assert "teaming play" in sales
    assert "client content" in sales
    assert "MegaPrime Corp" not in sales


# ---- monitor-grade adjacencies become teaming-watch plays ----------------------
def test_monitor_notice_becomes_a_teaming_watch_play(tmp_path, monkeypatch):
    """The pre-prime surface: a monitor-grade notice (M1) the client tracks
    but won't prime becomes a teaming-watch play via the ADJACENCY blocker —
    counted separately from pursue plays, candidates evidence-backed."""
    doc = _doc(tmp_path, monkeypatch)
    watch = [p for p in doc.partnering.plays if p.kind == "monitor"]
    assert doc.counts()["teaming_watch"] == len(watch) >= 1
    assert doc.counts()["teaming_plays"] == 1              # pursue (P2) counted apart
    m = next(p for p in watch if p.source_id == "M1")
    assert m.title == "Watch this" and m.directions == ["SUB_TO_PRIME"]
    assert any(b.kind == "ADJACENCY" and b.fired for b in m.blockers)
    assert m.candidates and all(c.evidence for c in m.candidates)

    internal = render_assessment(doc, "internal")
    assert "Teaming Watch</h2>" in internal
    assert "Watch this" in internal and "monitor-grade adjacency" in internal
    assert lint_counts(internal).ok


def test_monitor_watch_needs_evidence_no_candidates_no_play():
    """A monitor notice in a lane with no award evidence produces no teaming
    watch — never padded, and the section does not render."""
    s = copy.deepcopy(_partnering_searches())
    s["results"]["usaspending.gov"] = []      # strip lane awards -> no candidates
    s["results"]["subawards"] = {}
    doc = build_document("Testco", searches=s, qualify=None, as_of=AS_OF)
    assert doc.counts()["teaming_watch"] == 0
    assert "Teaming Watch</h2>" not in render_assessment(doc, "internal")


def test_teaming_watch_gated_in_sales(tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    sales = render_assessment(doc, "sales")
    assert "Teaming Watch</h2>" in sales and "teaming-watch" in sales.lower()
    assert "Verified against USAspending" in sales           # verifier line stays
    # notice title + candidate names are client content — gone from sales
    assert "Watch this" not in sales
    for p in (p for p in doc.partnering.plays if p.kind == "monitor"):
        for c in p.candidates:
            assert c.name not in sales

    gated = gate_for_sales(doc)
    assert gated.counts()["teaming_watch"] == doc.counts()["teaming_watch"]  # recomputes
    for p in (p for p in gated.partnering.plays if p.kind == "monitor"):
        assert p.title is None and p.candidates == [] and p.blockers == []
        assert p.locked and p.locked.verifiers.get("direction")
