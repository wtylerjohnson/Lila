"""Signal Board press runner: reproducible, gated, zero-editing (2026-07-16).

Doctrine: the default output IS the deliverable (no editor residue); a
gated press writes .DO-NOT-SEND plus the INTERNAL sidecar and exits
nonzero; --replay renders exclusively from stored inputs with both clocks
pinned, and two consecutive replays are byte-identical.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from test_assessment_document import _searches  # noqa: E402

import run_signal_board as runner  # noqa: E402


class _Args:
    def __init__(self, client="Testco", replay=True,
                 render_date="2026-07-13", out=None, offline=False):
        self.client = client
        self.replay = replay
        self.render_date = render_date
        self.out = out
        self.offline = offline


@pytest.fixture()
def pressroom(tmp_path, monkeypatch):
    cleaned = tmp_path / "data" / "cleaned"
    reports = tmp_path / "data" / "reports"
    cleaned.mkdir(parents=True)
    reports.mkdir(parents=True)
    sweep = _searches(2)
    sweep_path = cleaned / "searches_testco.json"
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")

    import agents.review as review
    monkeypatch.setattr(review, "sweep_artifact_path",
                        lambda _c: str(sweep_path))
    monkeypatch.setattr(runner, "REPORT_DIR", str(reports))
    import agents.reports.board_content as bc
    monkeypatch.setattr(bc, "load_board_content", lambda _c: None)
    return {"reports": reports}


@pytest.fixture()
def clean_link_pressroom(pressroom, monkeypatch):
    """Isolate link-gate outcomes from the fixture board's known empty-band
    conformance failure while retaining the real runner and sidecar wiring."""
    import agents.reports.signal_board as signal_board

    monkeypatch.setattr(
        signal_board,
        "render_signal_board",
        lambda _model: (
            "<!doctype html><html><body><section><h2>Opportunity detail</h2>"
            '<a href="https://agency.example/record">record</a>'
            "</section></body></html>"
        ),
    )
    monkeypatch.setattr(
        signal_board, "lint_signal_board", lambda _html: (True, []))
    return pressroom


def test_replay_requires_render_date(pressroom, capsys):
    assert runner._press(_Args(render_date=None)) == 2
    assert "--render-date" in capsys.readouterr().err


def test_two_replays_are_byte_identical(pressroom, tmp_path):
    out1 = str(tmp_path / "a.html")
    out2 = str(tmp_path / "b.html")
    runner._press(_Args(out=out1))
    runner._press(_Args(out=out2))
    b1, b2 = open(out1, "rb").read(), open(out2, "rb").read()
    assert hashlib.sha256(b1).hexdigest() == hashlib.sha256(b2).hexdigest()
    assert b1 == b2


def test_gated_press_stamps_do_not_send_and_writes_internal_sidecar(pressroom):
    """The derived-only fixture board has no figure provenance, so the
    computed data-current line is absent: conformance fails, the artifact
    is DO-NOT-SEND, and the INTERNAL sidecar carries the trail."""
    code = runner._press(_Args())
    assert code == 2
    reports = pressroom["reports"]
    names = sorted(os.listdir(reports))
    assert "testco.federal_opportunity_signals.DO-NOT-SEND.html" in names
    assert "testco.federal_opportunity_signals.internal.md" in names
    internal = (reports / "testco.federal_opportunity_signals.internal.md")
    text = internal.read_text(encoding="utf-8")
    assert "DO-NOT-SEND" in text
    assert "data-current" in text
    html = (reports / "testco.federal_opportunity_signals.DO-NOT-SEND.html")
    body = html.read_text(encoding="utf-8")
    assert "<script" not in body
    assert "no-export" not in body


def test_gated_press_never_reaches_the_desktop(pressroom, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert runner._press(_Args(replay=False, offline=True)) == 2
    assert not (tmp_path / "Desktop" / "Testco").exists()


def test_preset_scope_stamp_renders_on_every_relevance_sidecar_line(
        clean_link_pressroom, monkeypatch):
    import tools.relevance.scope as scope_module
    import tools.relevance.taxonomy as taxonomy_module

    taxonomy = taxonomy_module.load_taxonomy("Insignary")
    scope = scope_module.EngagementScope(preset="civilian", remove=["070"])
    monkeypatch.setattr(taxonomy_module, "load_taxonomy", lambda _client: taxonomy)
    monkeypatch.setattr(scope_module, "load_engagement_scope", lambda _client: scope)

    # The fixture is machine-screened with an unrelated taxonomy, so the
    # relevance gate correctly blocks; the INTERNAL trail must still stamp
    # every row before that refusal.
    assert runner._press(_Args()) == 2
    sidecar = (
        clean_link_pressroom["reports"]
        / "testco.federal_opportunity_signals.internal.md"
    ).read_text(encoding="utf-8")
    trail = sidecar.split("## Relevance trail", 1)[1]
    rows = [line for line in trail.splitlines() if line.startswith("- **")]
    assert rows
    assert all("scope preset:civilian@v1 (-070)" in row for row in rows)


def test_replay_makes_no_llm_or_live_call(pressroom, monkeypatch):
    """Structural: the press path never touches the composer or the live
    SAM verification helper; a call is an immediate failure."""
    import agents.decisions.maxplan_cli as cli

    def _bomb(*_a, **_k):
        raise AssertionError("replay called an LLM or live API")

    for name in ("run_claude", "call", "complete", "invoke"):
        if hasattr(cli, name):
            monkeypatch.setattr(cli, name, _bomb)
    import agents.reports.links as links
    monkeypatch.setattr(links, "verify_rewritten_sam_links", _bomb)
    import tools.api._http as http
    if hasattr(http, "get_json"):
        monkeypatch.setattr(http, "get_json", _bomb)
    runner._press(_Args())  # gated result is fine; a network call is not


def test_dead_external_link_blocks_with_url_and_section(
        clean_link_pressroom, monkeypatch):
    from agents.reports.link_integrity import LinkCheckResult, LinkClass
    import agents.reports.link_integrity as integrity

    url = "https://agency.example/dead-record"
    monkeypatch.setattr(
        integrity,
        "audit_client_links",
        lambda *_args, **_kwargs: [LinkCheckResult(
            url=url,
            classification=LinkClass.DEAD,
            sections=("Prospective horizon",),
            proof="GET 404 terminal https://agency.example/dead-record",
            status_code=404,
            final_url=url,
        )],
    )

    assert runner._press(_Args()) == 2
    reports = clean_link_pressroom["reports"]
    assert (
        reports / "testco.federal_opportunity_signals.DO-NOT-SEND.html"
    ).exists()
    sidecar = (
        reports / "testco.federal_opportunity_signals.internal.md"
    ).read_text(encoding="utf-8")
    assert "DEAD external link in section(s) Prospective horizon" in sidecar
    assert url in sidecar


def test_unverifiable_link_is_manual_check_and_does_not_block(
        clean_link_pressroom, monkeypatch):
    from agents.reports.link_integrity import LinkCheckResult, LinkClass
    import agents.reports.link_integrity as integrity

    url = "https://agency.example/access-controlled"
    monkeypatch.setattr(
        integrity,
        "audit_client_links",
        lambda *_args, **_kwargs: [LinkCheckResult(
            url=url,
            classification=LinkClass.UNVERIFIABLE,
            sections=("Evidence dock",),
            proof="GET 403 cannot verify access",
            status_code=403,
            final_url=url,
        )],
    )

    assert runner._press(_Args()) == 0
    reports = clean_link_pressroom["reports"]
    assert (
        reports / "testco.federal_opportunity_signals.html"
    ).exists()
    assert not (
        reports / "testco.federal_opportunity_signals.DO-NOT-SEND.html"
    ).exists()
    sidecar = (
        reports / "testco.federal_opportunity_signals.internal.md"
    ).read_text(encoding="utf-8")
    assert "## MANUAL LINK CHECK" in sidecar
    assert (
        f"- {url} · Evidence dock · GET 403 cannot verify access"
        in sidecar
    )
    assert "## Violations" not in sidecar


@pytest.mark.parametrize(
    ("replay", "offline"),
    ((True, False), (False, True)),
)
def test_replay_and_offline_modes_perform_zero_http(
        clean_link_pressroom, tmp_path, monkeypatch, replay, offline):
    import agents.reports.link_integrity as integrity

    def _bomb(*_args, **_kwargs):
        raise AssertionError("replay/offline constructed an HTTP client")

    monkeypatch.setattr(integrity.httpx, "Client", _bomb)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert runner._press(_Args(replay=replay, offline=offline)) == 0
    sidecar = (
        clean_link_pressroom["reports"]
        / "testco.federal_opportunity_signals.internal.md"
    ).read_text(encoding="utf-8")
    assert "## MANUAL LINK CHECK" in sidecar
    assert "checks disabled for replay/offline" in sidecar


def test_press_reconciles_and_emits_candidate_teaming_award_link(
        clean_link_pressroom, monkeypatch):
    """The card-level citation passes the real record/freshness press gate;
    a unit-materialized but unreconciled URL is not sufficient."""
    from agents.reports.board_content import BoardFigure, SignalBoardContent
    from agents.reports import board_content, signal_board

    gid = "CONT_AWD_2032H525F00130_2050_NNG15SC71B_8000"
    sweep_path = (clean_link_pressroom["reports"].parent / "cleaned"
                  / "searches_testco.json")
    sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    sweep["generated_at"] = "2026-07-13T12:00:00+00:00"
    sweep.setdefault("results", {})["award_repulls"] = [{
        "generated_id": gid,
        "award_id": "2032H525F00130",
        "amount": 1_660_329.76,
        "retrieved_at": "2026-07-13T12:00:00+00:00",
    }]
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")
    content = SignalBoardContent(
        client_name="Testco",
        teaming=[{
            "client": "TESTCO", "partner": "FCN",
            "target": "SolarWinds account",
            "angle": "SOLARWINDS AWARD AT IRS · THROUGH 12 MAY 2027",
            "proof": "AWARD 2032H525F00130 · VALIDATION-REQUIRED THESIS",
            "award_generated_id": gid,
            "citation_label": "award 2032H525F00130",
        }],
        figures=[BoardFigure(
            text="$1.66M", raw=1_660_329.76,
            source_system="usaspending",
            source_record_id="2032H525F00130",
            generated_internal_id=gid,
            retrieved_at=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
        )],
    )
    monkeypatch.setattr(
        board_content, "load_board_content", lambda _client: content)

    def render(model):
        row = model["teaming"][0]
        return ("<!doctype html><html><body><section>"
                "<h2>Candidate teaming opportunities</h2>"
                + signal_board._link(row["url"], row["citation_label"])
                + "</section></body></html>")

    monkeypatch.setattr(signal_board, "render_signal_board", render)

    assert runner._press(_Args()) == 0
    html = (clean_link_pressroom["reports"]
            / "testco.federal_opportunity_signals.html") \
        .read_text(encoding="utf-8")
    assert f'data-link-record="{gid}"' in html
    assert 'data-link-reconciled="1"' in html
    assert ">award 2032H525F00130</a>" in html


def test_missing_agency_claim_is_warning_only(
        clean_link_pressroom, monkeypatch):
    from agents.reports.board_content import BoardFigure, SignalBoardContent
    from agents.reports.link_integrity import LinkCheckResult, LinkClass
    import agents.reports.board_content as board_content
    import agents.reports.link_integrity as integrity

    url = "https://agency.example/record"
    content = SignalBoardContent(
        client_name="Testco",
        figures=[BoardFigure(
            text="$20B",
            raw=20_000_000_000,
            source_system="agency_doc",
            source_record_id="sewpvi-ceiling",
            source_url=url,
            retrieved_at=datetime(
                2026, 7, 13, 12, tzinfo=timezone.utc),
            analyst_attested=True,
        )],
    )
    monkeypatch.setattr(
        board_content, "load_board_content", lambda _client: content)
    monkeypatch.setattr(
        integrity,
        "audit_client_links",
        lambda *_args, **_kwargs: [LinkCheckResult(
            url=url,
            classification=LinkClass.OK,
            sections=("Figure provenance",),
            proof="GET 200 terminal https://agency.example/record",
            status_code=200,
            final_url=url,
            text="NASA awarded the contracts; no ceiling is stated here.",
        )],
    )

    assert runner._press(_Args()) == 0
    reports = clean_link_pressroom["reports"]
    assert not (
        reports / "testco.federal_opportunity_signals.DO-NOT-SEND.html"
    ).exists()
    sidecar = (
        reports / "testco.federal_opportunity_signals.internal.md"
    ).read_text(encoding="utf-8")
    assert "## AGENCY_DOC claim-visibility warnings" in sidecar
    assert (
        f"sewpvi-ceiling: value $20B is not visible in fetched text at {url}"
        in sidecar
    )
    assert "## Violations" not in sidecar
