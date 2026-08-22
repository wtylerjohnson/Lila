"""GOLDEN_BUILD Phase 3 tests: skeleton split, compose loop, mechanical
validator, press orchestration. Offline doctrine: fake run_claude, no
network, no LLM."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import compose as compose_mod  # noqa: E402
from agents.golden_press import skeleton as skeleton_mod  # noqa: E402
from agents.golden_press import validate as validate_mod  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "fixtures" / "golden" / "riverbed_golden.html"


# ---- skeleton -------------------------------------------------------------- #
def test_split_golden_round_trips_byte_exact():
    """The split is reversible: skeleton + content == the golden bytes.
    This is the design-inheritance guarantee."""
    html = GOLDEN.read_text(encoding="utf-8")
    skeleton, content = skeleton_mod.split_golden(html)
    assert skeleton_mod.splice(skeleton, content) == html
    # Red Hat full press (2026-07-24): hero, ticker, and the Next-steps
    # aside are CLIENT-IDENTITY surfaces and compose with the bands.
    assert content.startswith('<section class="sb-hero"')
    assert "sb-news-ticker" in content
    assert "sb-recommended" in content
    assert 'id="evidence"' in content
    # head/CSS/JS and the GTM utility chrome stay in the skeleton
    assert "<script" in skeleton
    assert "sb-utility" in skeleton
    assert "data:image/" in skeleton


def test_asset_elide_restore_round_trips_content_bytes():
    """Embedded portraits/seals leave the prompt copy and return verbatim."""
    html = GOLDEN.read_text(encoding="utf-8")
    _, content = skeleton_mod.split_golden(html)
    elided, assets = skeleton_mod.elide_assets(content)
    assert "data:image/" not in elided
    assert len(assets) == content.count('src="data:image/')
    assert skeleton_mod.restore_assets(elided, assets) == content


def test_split_fails_loud_on_missing_anchor():
    with pytest.raises(skeleton_mod.GoldenSplitError):
        skeleton_mod.split_golden("<html><body>no bands</body></html>")


def test_golden_itself_passes_the_well_formedness_check():
    """Calibration: the checker must accept the hand-made golden markup."""
    html = GOLDEN.read_text(encoding="utf-8")
    assert validate_mod.check_well_formed(html) == []


# ---- compose loop ---------------------------------------------------------- #
def _fake_run_factory(replies):
    calls = []

    def fake_run(prompt, *, system=None, model=None, timeout_s=None, **kw):
        calls.append({"prompt": prompt, "system": system, "model": model})
        return replies[min(len(calls) - 1, len(replies) - 1)]

    return fake_run, calls


def _mini_region(marker):
    """A complete hero + nine-band region (acceptance-gate shaped)."""
    bands = "".join(
        f'<section class="sb-band" id="{i}"><p>{marker} {i}</p></section>'
        # DECISION_RULES (2026-08-03): the grammar gained the decisions
        # band; the fixture tracks the contract it exists to pin.
        for i in ("forecast", "decisions", "candidate-review", "signals",
                  "accounts", "capabilities", "leadership", "method",
                  "calendar", "evidence"))
    return (f'<section class="sb-hero"><h1>{marker} hero</h1></section>'
            + bands)


@pytest.fixture(autouse=True)
def _small_gate_floor(monkeypatch):
    monkeypatch.setattr(compose_mod, "MIN_ACCEPTABLE_CHARS", 100)


def test_compose_stops_on_pass_and_appends_sentinel():
    fake_run, calls = _fake_run_factory([
        "```html\n" + _mini_region("draft") + "\n```",   # compose (fenced)
        "PASS",                                          # critique 1
    ])
    content, transcript = compose_mod.compose_content(
        "{}", "<section>golden</section>", run=fake_run, log=lambda m: None)
    assert len(calls) == 2
    assert content.startswith('<section class="sb-hero">')
    assert content.rstrip().endswith(compose_mod.SENTINEL)
    assert [t["step"] for t in transcript] == ["compose", "critique-1"]
    assert "PASS" in transcript[1]["verdict"]


def test_compose_revises_on_fix_list_then_passes():
    fake_run, calls = _fake_run_factory([
        _mini_region("draft v1"),
        "1. dock: record A1 missing; add it from the pack",
        _mini_region("draft v2 with A1") + "\n" + compose_mod.SENTINEL,
        "PASS",
    ])
    content, transcript = compose_mod.compose_content(
        "{}", "<golden/>", run=fake_run, log=lambda m: None)
    assert len(calls) == 4
    assert "draft v2" in content
    steps = [t["step"] for t in transcript]
    assert steps == ["compose", "critique-1", "revise-1", "critique-2"]
    # the revise call carried the critic's fix list
    assert "dock: record A1 missing" in calls[2]["prompt"]


def test_truncated_revision_is_rejected_and_prior_draft_survives():
    """Press-2 regression, pinned: a one-item fix list drew a 1,501-char
    patch reply; accepting it mutilated the draft and diverged the loop.
    The gate retries once with the completeness reminder, then keeps the
    intact prior draft."""
    fake_run, calls = _fake_run_factory([
        _mini_region("draft v1"),
        "1. tiny nit",
        "<p>just the fixed paragraph</p>",     # truncated revise, attempt 1
        "<p>still just a patch</p>",           # truncated revise, attempt 2
    ])
    content, transcript = compose_mod.compose_content(
        "{}", "<golden/>", run=fake_run, log=lambda m: None)
    assert len(calls) == 4
    assert "draft v1" in content               # prior draft survived
    assert compose_mod._COMPLETENESS_REMINDER.strip()[:40] in calls[3]["prompt"]
    steps = [t["step"] for t in transcript]
    assert steps == ["compose", "critique-1",
                     "revise-1-rejected", "revise-1-rejected"]


def test_validator_revision_names_every_violation():
    fake_run, calls = _fake_run_factory([
        _mini_region("fixed") + compose_mod.SENTINEL])
    out = compose_mod.revise_for_violations(
        _mini_region("bad"), "{}",
        [{"rule": "dollar_without_provenance", "detail": "$9 unknown"}],
        run=fake_run, log=lambda m: None)
    assert "dollar_without_provenance" in calls[0]["prompt"]
    assert out.rstrip().endswith(compose_mod.SENTINEL)


def test_incomplete_validator_revision_returns_prior_draft():
    prior = _mini_region("intact")
    fake_run, _ = _fake_run_factory(["<p>patch</p>", "<p>patch</p>"])
    out = compose_mod.revise_for_violations(
        prior, "{}", [{"rule": "x", "detail": "y"}],
        run=fake_run, log=lambda m: None)
    assert out == prior


# ---- validator ------------------------------------------------------------- #
def _pack():
    return EvidencePack(
        client_name="Acme Networks",
        generated_at="2026-07-24T18:00:00Z",
        records=[
            GoldenRecord(
                record_id="GS35F001", lane="L2_entity_award",
                title="AcmeFlow refresh", agency="GSA", recipient="ChannelCo",
                obligated_dollars=1_923_540.0, period_start="2025-01-15",
                period_end="2026-07-31", research_clock=True,
                vehicle="SEWP V", url="https://www.usaspending.gov/award/CONT_AWD_X",
                entity_hits=["AcmeFlow"]),
            GoldenRecord(
                record_id="F2026074529", lane="L4_forecast",
                title="Observability platform support", agency="DHS",
                estimated_value_range="$1M to $5M",
                anticipated_solicitation="Q2 2026",
                url="https://apfs-cloud.dhs.gov/forecast/74529"),
        ])


def _valid_content(pack):
    a, b = pack.records
    return f"""
<div id="board">><h1>Acme Networks · federal signal</h1>
 <div class="sb-news-ticker"><a href="{a.url}">GS35F001</a></div></section>
<section class="band" id="forecast"><h2>Landscape</h2>
 <p>Total obligated across the pack is $1,923,540 with one live clock.</p>
 <p><a href="{a.url}">{a.record_id}</a> · <a href="{a.url}">AcmeFlow refresh</a>
 renews by 2026-07-31; ChannelCo holds $1,923,540 on SEWP V since 2025-01-15.</p>
</section>
<section class="band" id="decision"><p>Decision rules were not computed
 for this evidence pack; it was pressed before the deterministic decision
 rules existed.</p></section>
<section class="band" id="candidate-review"><p>Candidate story for
 <a href="{a.url}">{a.record_id}</a> only, cited once more here.</p></section>
<section class="band" id="signals"><p>Forecast signal:
 <a href="{b.url}">{b.record_id}</a> · <a href="{b.url}">Observability
 platform support</a> at $1M to $5M in Q2 2026.</p></section>
<section class="band" id="accounts"><p>GSA and DHS corridors.</p></section>
<section class="band" id="capabilities"><p>Product language families.</p></section>
<section class="band" id="leadership"><p>Account ownership to validate.</p></section>
<section class="band" id="method"><p>Assess, Target, Execute.</p></section>
<section class="band" id="calendar"><h2>Federal opportunity calendar</h2>
 <div>2026-07-31 · AWARD CLOCK · <a href="{a.url}">{a.record_id}</a> · GSA</div>
 <div>Q2 2026 · FORECAST WINDOW · <a href="{b.url}">{b.record_id}</a> · DHS</div>
</section>
<section class="band" id="evidence"><h2>Evidence dock</h2>
 <div>01 <a href="{a.url}">{a.record_id}</a></div>
 <div>02 <a href="{b.url}">{b.record_id}</a></div>
</section>
{validate_mod.SENTINEL}"""


def test_validator_accepts_the_renderers_own_output():
    """CONTRACT (2026-08-03): the deterministic renderer is the conformant
    producer; its output must satisfy every validator rule."""
    from agents.golden_press.render import render_content_region
    pack = _pack()
    content = render_content_region(pack, prose={})
    verdict = validate_mod.validate_press(content, content, pack)
    assert verdict["violations"] == []
    assert verdict["ok"] is True


@pytest.mark.parametrize(
    ("inject", "rule"),
    [
        ("<p>The award grew to $2,500,000 with options.</p>",
         "dollar_without_provenance"),
        ("<p>It renews by 2026-09-15 on schedule.</p>",
         "date_without_provenance"),
        ("<p>See contract W91278ABC1234 for scope.</p>",
         "contract_number_without_provenance"),
        ("<p>Assess — Target — Execute.</p>", "emdash_in_copy"),
        ("<p>This buying account depends on the 360° assessment.</p>",
         "banned_vocabulary"),
    ],
)
def test_validator_flags_each_defect_class(inject, rule):
    """dock_record_count and calendar_missing_record are RETIRED with their
    bands (REPORT_CONTRACT.md §1); inline linkage, exactly-once titles, and
    the clocks-band checks are their successors."""
    from agents.golden_press.render import render_content_region
    pack = _pack()
    content = render_content_region(pack, prose={})
    marker = '<section class="band" id="method"'
    mutated = content.replace(marker, inject + marker, 1)
    verdict = validate_mod.validate_press(mutated, mutated, pack)
    assert any(v["rule"] == rule for v in verdict["violations"]), (
        rule, verdict["violations"])


def test_validator_flags_unlinked_and_noncanonical_ids():
    from agents.golden_press.render import render_content_region
    pack = _pack()
    content = render_content_region(pack, prose={})
    rid = pack.records[0].record_id
    marker = '<section class="band" id="method"'
    naked = content.replace(marker, f"<p>{rid}</p>" + marker, 1)
    verdict = validate_mod.validate_press(naked, naked, pack)
    assert any(v["rule"] == "record_id_not_linked"
               for v in verdict["violations"])
    wrong = content.replace(
        marker,
        f'<p><a href="https://example.gov/x">{rid}</a></p>' + marker, 1)
    verdict = validate_mod.validate_press(wrong, wrong, pack)
    assert any(v["rule"] == "record_link_not_canonical"
               for v in verdict["violations"])


def test_validator_flags_a_missing_sentinel():
    from agents.golden_press.render import render_content_region
    pack = _pack()
    content = render_content_region(pack, prose={})
    verdict = validate_mod.validate_press(
        content, content.replace(validate_mod.SENTINEL, ""), pack)
    assert any(v["rule"] == "sentinel_missing"
               for v in verdict["violations"])


def test_validator_flags_repeated_prose_across_records():
    pack = _pack()
    boiler = ("This award demonstrates sustained federal demand for the "
              "capability and merits immediate review.")
    content = _valid_content(pack).replace(
        "renews by 2026-07-31;", f"renews by 2026-07-31; {boiler}").replace(
        "at $1M to $5M in Q2 2026.", f"at $1M to $5M in Q2 2026. {boiler}")
    verdict = validate_mod.validate_press(content, content, pack)
    assert any(v["rule"] == "repeated_prose" for v in verdict["violations"])


def test_brand_skeleton_swaps_identity_chrome_exactly():
    """Red Hat full press: skeleton title/meta/report-id/download/brand
    strings rebrand for a non-golden client; the golden client's actual
    logo never ships (slot goes neutral); every swap is exact-count."""
    html = GOLDEN.read_text(encoding="utf-8")
    skeleton, _ = skeleton_mod.split_golden(html)
    branded = skeleton_mod.brand_skeleton(
        skeleton, client_name="Red Hat", slug="red_hat", stamp="2026-07-24")
    assert "<title>Red Hat · Federal Opportunity Pre-Assessment</title>" in branded
    assert 'data-report-id="red_hat-candidate-review-golden-2026-07-24"' in branded
    assert "Red_Hat_Federal_Opportunity_Assessment" in branded
    assert '<span class="sb-brand-word">RED HAT</span>' in branded
    assert 'data-logo-id="header-red_hat-logo"' in branded
    import re
    assert not re.search(r"[Rr]iverbed", re.sub(
        r'\|\| "riverbed[^"]*"|\|\| "Riverbed[^"]*"', "", branded)), \
        "no golden-client identity may survive outside dead JS fallbacks"
    with pytest.raises(skeleton_mod.GoldenSplitError):
        skeleton_mod.brand_skeleton(
            branded, client_name="X", slug="x", stamp="2026-07-24")


def _tile(figure, *, caption=True, toggle=True, title=True, terminal=None,
          href="https://www.usaspending.gov/award/CONT_AWD_X"):
    """One sb-signal tile in the show-your-work pattern (2026-07-25 law)."""
    parts = [f'<div class="sb-signal"><div class="sb-label">L</div>'
             f'<strong><span data-edit-id="m">{figure}</span></strong>']
    if caption:
        parts.append('<span class="lila-qualifier">Obligated across 2 cited '
                     'award records · not pipeline revenue</span>')
    parts.append('<details class="lila-just"><summary>')
    if toggle:
        parts.append('<span class="lila-just-toggle lila-just-more">Show the '
                     'work +</span><span class="lila-just-toggle '
                     'lila-just-less">Hide the work −</span>')
    parts.append('</summary><div class="lila-just-body">')
    if title:
        parts.append('<span class="lila-just-title">How the total was built'
                     '</span>')
    parts.append(f'<span class="lila-just-row"><span class="lila-just-op">+'
                 f'</span><a href="{href}">GSA × Acme via ChannelCo</a> · '
                 f'$1,923,540.00</span>')
    parts.append(f'<span class="lila-just-total">= Exact total '
                 f'{terminal or figure}</span>')
    parts.append("</div></details></div>")
    return "".join(parts)


def test_aggregate_justification_law_is_enforced():
    """SHOW-YOUR-WORK LAW (2026-07-25), same standing as LINK CHECK: an
    aggregate needs a caption, a VISIBLE toggle, a titled panel whose
    components anchor to pack source_urls, and a terminal figure matching
    the headline character for character."""
    pack = _pack()
    url = pack.records[0].url
    ok = _tile("$1.92M", href=url)
    assert validate_mod.check_aggregate_justification(ok, pack) == []

    def rules(content):
        return {v["rule"] for v in
                validate_mod.check_aggregate_justification(content, pack)}

    assert "aggregate_uncaptioned" in rules(_tile("$1.92M", caption=False, href=url))
    assert "aggregate_toggle_missing" in rules(_tile("$1.92M", toggle=False, href=url))
    assert "aggregate_panel_untitled" in rules(_tile("$1.92M", title=False, href=url))
    # terminal must restate the headline character for character
    assert "aggregate_terminal_mismatch" in rules(
        _tile("$1.92M", terminal="$1.9M", href=url))
    # components must cite pack source_urls
    assert "aggregate_components_unanchored" in rules(
        _tile("$1.92M", href="https://example.com/not-a-pack-url"))
    # a bare figure with no panel at all is the headline violation
    bare = ('<div class="sb-signal"><div class="sb-label">L</div>'
            '<strong><span data-edit-id="m">$1.92M</span></strong></div>')
    assert "aggregate_unjustified" in rules(bare)
    # count tiles carry no dollar claim and are exempt
    counts = ('<div class="sb-signal"><div class="sb-label">L</div>'
              '<strong><span data-edit-id="m">46</span></strong></div>')
    assert validate_mod.check_aggregate_justification(counts, pack) == []


def test_band_grammar_rejects_renamed_bands():
    """Live-press regression, pinned: the composer once renamed bands
    (thesis/corridors/footprint/competition); skeleton nav anchors broke.
    The exact golden band ids, in order, are the contract."""
    from agents.golden_press.render import render_content_region
    pack = _pack()
    good = render_content_region(pack, prose={})
    assert validate_mod.check_band_grammar(good) == []
    renamed = good.replace('<section class="band" id="decisions"',
                           '<section class="band" id="thesis"')
    violations = validate_mod.check_band_grammar(renamed)
    assert violations and violations[0]["rule"] == "band_grammar"
    assert "thesis" in violations[0]["detail"]


# test_calendar_must_sit_between_method_and_dock: RETIRED with the
# calendar band (REPORT_CONTRACT.md §1); Band 03 clocks carries the
# successor rules, pinned in test_golden_render.


# ---- press orchestration (offline) ----------------------------------------- #
def _press_fixture(monkeypatch, pack):
    import agents.golden_press.press as press_mod
    import agents.review as review_mod

    class _Packet:
        strategy = type("S", (), {"research_entities": [], "client_name":
                                  "Acme Networks"})()
        status = type("St", (), {"value": "approved"})()

    monkeypatch.setattr(review_mod, "load_packet", lambda c: _Packet())
    monkeypatch.setattr(press_mod, "build_evidence_pack", lambda *a, **k: pack)
    import tools.relevance.scope as scope_mod
    monkeypatch.setattr(scope_mod, "load_engagement_scope", lambda c: None)
    return press_mod


def test_press_renders_deterministically_and_writes_artifacts(
        tmp_path, monkeypatch):
    pack = _pack()
    press_mod = _press_fixture(monkeypatch, pack)
    fake_run, calls = _fake_run_factory([json.dumps({
        "hero_context": "This account sits in one bureau with a live clock "
                        "and a named rival already inside the estate."})])
    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=fake_run, log=lambda m: None)
    html = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "GS35F001" in html and "sb-news-ticker" in html   # spliced
    assert result["validation"]["ok"] is True
    assert result["certified"] is True
    assert Path(result["sidecars"]["critique"]).exists()
    assert Path(result["sidecars"]["evidence_pack"]).exists()
    assert Path(result["sidecars"]["prose"]).exists()
    prose_sidecar = json.loads(Path(result["sidecars"]["prose"]).read_text(
        encoding="utf-8"))
    assert prose_sidecar["schema_version"] == 1
    assert prose_sidecar["client_name"] == "Acme Networks"
    assert prose_sidecar["prose"]["hero_context"].startswith(
        "This account sits")
    assert "/report?path=" in result["cc_link"]
    # ONE model call for the whole region, and it asked for prose only.
    assert len(calls) == 1
    assert "prose" in calls[0]["system"].lower()


def test_rerender_loads_pack_bound_prose_and_reproduces_html_bytes(
        tmp_path, monkeypatch):
    """The sealed HTML is reproducible without another model call."""
    pack = _pack()
    press_mod = _press_fixture(monkeypatch, pack)
    fake_run, calls = _fake_run_factory([json.dumps({
        "hero_context": "This account sits in one bureau with a live clock "
                        "and a named rival already inside the estate.",
        "band_candidates": "The nearest clock deserves review because the "
                           "buyer and route are already visible in the record.",
    })])
    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=fake_run, log=lambda m: None)
    original = Path(result["html_path"]).read_bytes()
    original_client = Path(result["client_path"]).read_bytes()
    assert len(calls) == 1

    Path(result["html_path"]).write_text("stale", encoding="utf-8")
    # A re-render must not strand the prior client export either: the client
    # build is generated from press state, and a re-render IS new press
    # state. A certifying replay that left the old client file handed the
    # operator a certified verdict stapled to a stale client artifact
    # (witnessed 2026-08-05, the bare-0002 build).
    Path(result["client_path"]).write_text("stale", encoding="utf-8")
    replay = press_mod.rerender_golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        log=lambda m: None)

    assert Path(replay["html_path"]).read_bytes() == original
    assert Path(replay["client_path"]).read_bytes() == original_client
    assert replay["sidecars"]["prose"] == result["sidecars"]["prose"]


def test_rerender_refuses_prose_bound_to_another_pack(tmp_path, monkeypatch):
    pack = _pack()
    press_mod = _press_fixture(monkeypatch, pack)
    fake_run, _ = _fake_run_factory([json.dumps({
        "hero_context": "This account sits in one bureau with a live clock "
                        "and a named rival already inside the estate.",
    })])
    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=fake_run, log=lambda m: None)
    prose_path = Path(result["sidecars"]["prose"])
    sidecar = json.loads(prose_path.read_text(encoding="utf-8"))
    sidecar["evidence_pack_sha256"] = "0" * 64
    prose_path.write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(press_mod.GoldenPressError,
                       match="another evidence pack"):
        press_mod.rerender_golden_press(
            "Acme Networks", root=REPO, state_root=tmp_path,
            log=lambda m: None)


def test_press_cannot_take_a_figure_from_the_model(tmp_path, monkeypatch):
    """The point of deterministic rendering: a model that hands back a
    fabricated dollar figure cannot get it onto the page, because code
    renders every figure and prose carrying a digit is discarded."""
    pack = _pack()
    press_mod = _press_fixture(monkeypatch, pack)
    poisoned = json.dumps({
        "hero_context": "This estate is worth $9,999,999 across 47 awards.",
        "band_forecast": "Contract W912DY99D0001 renews in 2031 for sure.",
        "next_steps": "Chase the $9,999,999 renewal before September 2031.",
    })
    fake_run, _ = _fake_run_factory([poisoned])
    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=fake_run, log=lambda m: None)
    html = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "9,999,999" not in html
    assert "W912DY99D0001" not in html
    assert "2031" not in html
    assert result["certified"] is True


def test_prose_failure_never_blocks_the_press(tmp_path, monkeypatch):
    """A dead prose call costs the report its writing, never its evidence."""
    pack = _pack()
    press_mod = _press_fixture(monkeypatch, pack)

    def exploding_run(*a, **k):
        raise RuntimeError("plan usage ceiling")

    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=exploding_run, log=lambda m: None)
    html = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "GS35F001" in html
    assert result["certified"] is True


def test_press_ships_the_report_even_when_validation_fails(
        tmp_path, monkeypatch):
    """Operator doctrine 2026-07-27: no blocking, degrade instead, the report
    always gets produced. A violation ships a flagged copy and certified
    False; it never costs the operator the deliverable."""
    pack = _pack()
    press_mod = _press_fixture(monkeypatch, pack)
    monkeypatch.setattr(press_mod, "validate_press", lambda *a, **k: {
        "ok": False,
        "violations": [{"rule": "invented_for_this_test", "detail": "x"}]})
    fake_run, _ = _fake_run_factory(["{}"])
    result = press_mod.golden_press(
        "Acme Networks", root=REPO, state_root=tmp_path,
        run=fake_run, log=lambda m: None)
    assert Path(result["html_path"]).exists(), "the report is always produced"
    assert result["certified"] is False, "but it is not certified"
    assert list(tmp_path.glob("*.FAILED.html")), "flagged copy kept alongside"
    assert json.loads(Path(result["sidecars"]["validation"]).read_text(
        encoding="utf-8"))["violations"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_the_same_sentence_cannot_fill_two_slots(tmp_path):
    """The fallback model wrote one identical line into six record slots on a
    live press and the validator caught it as repeated_prose. Prose is
    optional, so a duplicate loses its slot to the deterministic fallback."""
    payload = json.dumps({
        "hero_context": "The estate concentrates in a single civilian bureau.",
        "band_forecast": "The estate concentrates in a single civilian bureau.",
        "band_signals": "A different and entirely separate observation here.",
    })
    run, _ = _fake_run_factory([payload])
    kept, _ = compose_mod.compose_prose(
        "{}", [{"key": k, "brief": "b"} for k in
               ("hero_context", "band_forecast", "band_signals")],
        run=run, log=lambda m: None)
    assert "hero_context" in kept
    assert "band_forecast" not in kept, "the duplicate must not ship"
    assert "band_signals" in kept


def test_linked_verbatim_source_ids_carry_their_own_provenance():
    # 2026-08-04 Varonis press: notices that print their own solicitation
    # number ("The notice number for this solicitation is 9594CS26Q0025")
    # rendered as linked verbatim titles and the scanner called them
    # unprovenanced. Inside an anchor, the link IS the citation.
    from types import SimpleNamespace
    pack = SimpleNamespace(records=())
    linked = ('<p><a href="https://sam.gov/opp/x/view">N0018918R0007 '
              "Revised Industry Day Information</a></p>")
    assert validate_mod.check_contract_numbers(linked, pack) == []
    loose = "<p>see also N0018918R0007 for details</p>"
    rules = [v.rule if hasattr(v, "rule") else v["rule"]
             for v in validate_mod.check_contract_numbers(loose, pack)]
    assert "contract_number_without_provenance" in rules


def test_short_record_ids_only_match_word_bounded_visible_text():
    # "0023" must not be found inside another award's PIID or a URL.
    from types import SimpleNamespace
    record = SimpleNamespace(record_id="0023",
                             parent_award_id=None,
                             url="https://www.usaspending.gov/award/A")
    pack = SimpleNamespace(records=(record,))
    content = ('<p><a href="https://www.usaspending.gov/award/A">0023</a>'
               " alongside SPE7M5160023X and CONT_AWD_50023</p>")
    assert validate_mod.check_links(content, pack) == []
    bare = content + "<p>award 0023 closes soon</p>"
    rules = [v.rule if hasattr(v, "rule") else v["rule"]
             for v in validate_mod.check_links(bare, pack)]
    assert "record_id_not_linked" in rules


def test_treatment_rows_citing_one_award_twice_are_not_boilerplate():
    # 2026-08-04 Riverbed press: two displacement cards both anchored to
    # the client's own CBP award and the identical deterministic
    # "Client history here: <link>" rows read as repeated_prose. Treatment
    # rows are structure, not composed prose.
    row = ('<div class="sb-treatment">Client history here: '
           '<a href="https://www.usaspending.gov/award/X">'
           "70B04C18F00000184</a> DISPLACEMENT SIGNAL HOLDS ACROSS THE "
           "BUYER RELATIONSHIP</div>")
    html = f"<section>{row}{row}</section>"
    assert validate_mod.check_repeated_prose(html) == []
    prose = ("<p>the very same forty-eight character sentence repeats "
             "here twice.</p>")
    assert validate_mod.check_repeated_prose(
        f"<section>{prose}{prose}</section>") != []


# ---- composer wrong-domain attestation (2026-08-04) ------------------------ #
def _rival_pack():
    return EvidencePack(
        client_name="Varonis",
        generated_at="2026-08-04T18:00:00Z",
        research={"entities": {"competitor": ["Sentra"]}},
        records=[
            GoldenRecord(
                record_id="0023", lane="L2_entity_award",
                title="8504681597!PROBE,GASKON SENTRA",
                agency="Department of Defense",
                sub_agency="Defense Logistics Agency",
                recipient="SZY HOLDINGS, LLC", obligated_dollars=6759.0,
                url="https://www.usaspending.gov/award/CONT_AWD_0023",
                entity_hits=["Sentra"]),
            GoldenRecord(
                record_id="GS35F900", lane="L2_entity_award",
                title="Varonis DatAdvantage renewal", agency="GSA",
                recipient="Carahsoft", obligated_dollars=250_000.0,
                url="https://www.usaspending.gov/award/CONT_AWD_Y",
                entity_hits=["Varonis"]),
        ])


def test_composer_wrong_domain_attestation_demotes_only_rival_seats():
    from agents.golden_press import render as render_mod

    pack = _rival_pack()
    prose = {
        "why_0023": ("WRONG DOMAIN: this is a gas-detection probe part, "
                     "not the data-security vendor."),
        "why_GS35F900": ("WRONG DOMAIN: prefix on a core record never "
                         "demotes, affinity guards it."),
    }
    demoted = render_mod.composer_demotions(pack, prose)
    assert set(demoted) == {"0023"}
    assert demoted["0023"].startswith("this is a gas-detection probe")

    s = render_mod.shape(pack, demoted=frozenset(demoted))
    assert all(r.record_id != "0023" for r in s["rival"])
    assert any(r.record_id == "0023" for r in s["records"])  # dock keeps it

    # The displacement SEAT goes too. Retargeted 2026-08-05 from the
    # retired render_decisions to the contract's Band 01 renderer; the
    # demotion contract is unchanged, only the band that hosts it.
    s["decisions"] = {"r2": {"alerts": [{
        "kind": "pack_award", "record_id": "0023", "competitor": "Sentra",
        "agency": "Defense Logistics Agency", "title": "PROBE",
        "url": "https://www.usaspending.gov/award/CONT_AWD_0023",
        "matched_sentence": "PROBE,GASKON SENTRA",
        "client_history_record_ids": [],
    }]}}
    html = render_mod.render_decision_band(1, s, {})
    assert "PROBE,GASKON SENTRA" not in html   # the seat is gone
    assert 'id="decisions"' in html            # the band still renders
    # and the zero is stated, never padded
    assert ("COMPETITIVE VALIDATION" in html
            or "HEAD-TO-HEAD" in html
            or "DISPLACEMENT · NONE" in html)


def test_rival_slot_briefs_carry_the_attestation_invitation():
    from agents.golden_press import render as render_mod

    slots = {s["key"]: s["brief"] for s in render_mod.prose_slots(_rival_pack())}
    assert render_mod.WRONG_DOMAIN_PREFIX in slots["why_0023"]
    assert render_mod.WRONG_DOMAIN_PREFIX not in slots["why_GS35F900"]
