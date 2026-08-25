"""Dashboard view modes: internal default, server-rendered sales mode from
the gated model, and the mechanical leak test extended to dashboard routes.

Offline — temp data dirs, Flask test client, no subprocesses, no LLM.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

flask = pytest.importorskip("flask")

import ui.server as srv  # noqa: E402
from tests.test_assessment_document import _searches  # noqa: E402
from tests.test_ui import _mk_client  # noqa: E402

SLUG = "acme_federal"

# client-only strings from the P1/P2/P3 fixture — the same set the standalone
# sales leak test uses (competitor names, non-#1 titles, notice links)
FIXTURE_SECRETS = ("AVI-SPL", "GDIT", "Diversified",
                   "Courtroom AV Refresh", "sam.gov/opp/P2")


class _FixtureDate(date):
    """Keep the fixed-2026 opportunity fixture on its declared as-of date."""

    @classmethod
    def today(cls):
        return cls(2026, 7, 6)


def _replace_fixture_federal_urls(value):
    """Keep unrelated route tests off the federal construction boundary."""
    if isinstance(value, dict):
        return {key: _replace_fixture_federal_urls(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_fixture_federal_urls(item) for item in value]
    if isinstance(value, str):
        return (value
                .replace("https://sam.gov/", "https://records.example.gov/")
                .replace("https://www.usaspending.gov/award/",
                         "https://records.example.gov/award/"))
    return value


def _seed(tmp_path, monkeypatch, with_assessment=True, *, safe_links=False):
    from agents.assess import live_report
    from agents.reports import document

    # These route fixtures carry fixed July 2026 deadlines. Letting wall-clock
    # time drive their ranks makes dossier numbering change as those dates
    # pass, even though the route behavior under test has not changed.
    monkeypatch.setattr(document, "date", _FixtureDate)
    monkeypatch.setattr(
        live_report, "utc_today", lambda: _FixtureDate(2026, 7, 6))
    root = str(tmp_path)
    review = _mk_client(root)
    monkeypatch.setattr(srv, "REVIEW_DIR", review)
    for sub in ("cleaned", "reports"):
        os.makedirs(os.path.join(root, "data", sub), exist_ok=True)
    monkeypatch.setattr(srv, "CLEANED_DIR", os.path.join(root, "data", "cleaned"))
    monkeypatch.setattr(srv, "REPORT_DIR", os.path.join(root, "data", "reports"))
    # These legacy route tests isolate their named render/link/attachment gate.
    # Dedicated Targeting Review tests exercise the new upstream gate.
    monkeypatch.setattr(
        srv, "_client_targets_payload",
        lambda _slug: ({"targeting_readiness": {"ready": True,
                                                 "problems": []}}, 200),
    )
    # Route tests are offline; dedicated boundary tests below inject explicit
    # DEAD/UNVERIFIABLE outcomes without touching the network.
    monkeypatch.setattr(srv, "CLIENT_LINK_CHECKS_ENABLED", False)
    monkeypatch.setenv("LILA_DESKTOP_ROOT", os.path.join(root, "nodesk"))
    if with_assessment:
        searches = _searches(3)
        if safe_links:
            searches = _replace_fixture_federal_urls(searches)
        with open(os.path.join(root, "data", "cleaned", f"searches_{SLUG}.json"), "w") as f:
            json.dump(searches, f)
    return srv.app.test_client()


def test_internal_is_the_default_view(tmp_path, monkeypatch):
    """Opening a client opens the INTERNAL view: the existing dashboard."""
    client = _seed(tmp_path, monkeypatch)
    r = client.get(f"/client/{SLUG}")
    assert r.status_code == 200
    assert b"LILA CONTROL ROOM" in r.data          # the existing SPA, unchanged
    assert b"v-locked" not in r.data               # no gating in the shell


def test_sales_mode_renders_server_side_from_the_gated_model(tmp_path, monkeypatch):
    """The dashboard leak test: fetch the sales-mode page, assert no
    client-only string from the full model appears — while the verifier
    fields (the anti-fake mechanism) DO appear."""
    from agents.reports.lint import lint_whitelabel

    client = _seed(tmp_path, monkeypatch)
    # client view law (2026-07-18): the sales view is retired from the UI,
    # flag-gated off by default; the backend stays intact behind the flag,
    # so this leak test runs with the flag raised and the default asserts
    # the retirement.
    assert client.get(f"/client/{SLUG}/sales").status_code == 404
    monkeypatch.setattr(srv, "SALES_VIEW_ENABLED", True)
    r = client.get(f"/client/{SLUG}/sales")
    assert r.status_code == 200
    html = r.data.decode()

    for secret in FIXTURE_SECRETS:                  # nothing paid leaks
        assert secret not in html, f"dashboard sales mode leaked: {secret}"
    assert "Video Wall Modernization" in html       # #1 name stays (ratified)
    assert "Verified against SAM.gov" in html       # verifier lines present
    assert "70RSAT26R000XXXX" in html               # masked id, real prefix
    assert 'data-locked="1"' in html                # visibly locked, not absent
    assert "part of the client engagement" in html  # verification footer
    assert "/client/acme_federal" in html           # toggle back to internal
    assert lint_whitelabel(html).ok                 # white-label on the route


def test_banner_renders_only_after_an_assessment(tmp_path, monkeypatch):
    """No assessment -> no banner, never an empty scoreboard."""
    client = _seed(tmp_path, monkeypatch, with_assessment=False)
    assert client.get(f"/api/client/{SLUG}/banner").get_json()["html"] == ""
    monkeypatch.setattr(srv, "SALES_VIEW_ENABLED", True)  # flag-gated route
    assert b"vb-board" not in client.get(f"/client/{SLUG}/sales").data

    client2 = _seed(tmp_path, monkeypatch, with_assessment=True)
    html = client2.get(f"/api/client/{SLUG}/banner").get_json()["html"]
    assert 'class="vb-board"' in html and 'data-view="internal"' in html


def test_banner_counts_come_from_counts(tmp_path, monkeypatch):
    """Every count in banner copy is counts() ground truth, both views."""
    import json as _json
    from agents.reports.document import build_document
    from agents.reports.views import gate_for_sales, render_scoreboard

    doc = build_document("Testco", searches=_searches(3), qualify=None)
    c = doc.counts()
    internal = render_scoreboard(doc, "internal")
    assert f'{c["agencies"]} relevant agenc' in internal
    assert f'{c["competitors"]}</span> competitor' in internal
    # badges slice the same basis as the headline — here the addressable pull
    assert internal.count('class="vb-badge"') == min(6, len(doc.market.addressable_agency_annual))
    assert internal.count('class="vb-sol"') == min(3, c["pursuits"])

    # grounded TAM: the keyword-scoped (addressable) slice leads when the
    # pull carried it, with the lane total as stated context — never the raw
    # 3-yr pile sold as "identified opportunity market"
    assert f"{doc.market.addressable_annual_label}/yr" in internal   # $3.0M/yr headline
    assert "keyword-matched federal spend" in internal
    # the number shows its math (2026-07-10): derivation on the banner,
    # lane figure demoted to labeled context
    assert "How this number is built:" in internal
    assert "divided by 3" in internal
    assert f"total lane obligations run {doc.market.tam_annual_label}/yr" in internal
    assert "identified opportunity market" not in internal
    assert "$2.0M/yr" in internal                     # DoD badge, same basis

    # older sweep without the pull: falls back to the top-award pool basis
    import copy as _copy
    s2 = _copy.deepcopy(_searches(3))
    s2["results"]["usaspending.gov"][0].pop("addressable")
    doc2 = build_document("Testco", searches=s2, qualify=None)
    fallback = render_scoreboard(doc2, "internal")
    assert f"{doc2.market.tam_annual_label}/yr" in fallback          # $40.0M/yr
    assert "avg federal obligations per year" in fallback
    assert "market context, not a client forecast" in fallback
    assert "$1.7M/yr" in fallback                     # pool badge basis

    sales = render_scoreboard(doc, "sales")
    gated = gate_for_sales(doc)
    gc = gated.counts()
    assert (f'plus {gc["watchlist_entries"]} more '
            f'item{"s" if gc["watchlist_entries"] != 1 else ""}') in sales
    # sales banner: verifiers on every locked solicitation card, dollars locked
    assert sales.count("Verified against SAM.gov") == min(3, gc["pursuits"])
    assert "vb-lockmark" in sales and "$" not in _json.dumps(
        [x for x in sales.split('vb-badge') if "vb-bdollar" in x and "lockmark" not in x])
    # per-agency dollars (pool AND addressable slices) are client content:
    # physically gone from the gated model, so sales badges cannot leak them
    assert all(a.dollars is None and a.dollars_label is None
               for a in gated.market.award_agency_annual)
    assert all(a.dollars is None and a.dollars_label is None
               for a in gated.market.addressable_agency_annual)
    assert "$1.7M" not in sales and "$2.0M" not in sales
    # the addressable headline stays visible in sales (TAM-headline spec)
    assert f"{doc.market.addressable_annual_label}/yr" in sales
    # competitor award rows (ids + USAspending links) are client content
    assert all(c.awards == [] for c in gated.competitive.competitors)
    assert "W91ABC24C0001" not in sales


def test_competitor_drilldown_endpoint(tmp_path, monkeypatch):
    """Every drill-down figure traces to award rows; no assessment -> empty."""
    # bare seed FIRST: the two seeds share tmp_path, and the full seed's
    # assessment artifact would otherwise still be on disk for the bare check
    bare = _seed(tmp_path, monkeypatch, with_assessment=False)
    empty = bare.get(f"/api/client/{SLUG}/competitors").get_json()
    assert empty["competitors"] == []

    client = _seed(tmp_path, monkeypatch)
    data = client.get(f"/api/client/{SLUG}/competitors").get_json()
    assert data["coverage_note"]
    by_name = {c["name"]: c for c in data["competitors"]}
    avi = by_name["AVI-SPL"]
    assert avi["dollars_label"] == "$5.0M" and avi["annual_label"] == "$1.7M"
    assert avi["awards"][0]["award_id"] == "W91ABC24C0001"
    assert avi["awards"][0]["url"].startswith("https://www.usaspending.gov/award/")
    assert by_name["GDIT"]["awards"] == []      # subaward prime: basis says why
    assert "subaward" in by_name["GDIT"]["basis"]


def test_ticker_pauses_on_hover_and_respects_reduced_motion(tmp_path, monkeypatch):
    from agents.reports.document import build_document
    from agents.reports.views import render_scoreboard

    doc = build_document("Testco", searches=_searches(3), qualify=None)
    html = render_scoreboard(doc, "internal")
    assert "vb-ticker" in html
    assert "animation-play-state:paused" in html          # hover pause
    assert "prefers-reduced-motion" in html               # static when set
    assert "@media print" in html                         # static on paper
    assert 'aria-hidden="true"' in html                   # dup tape is decor


def test_agency_badge_seal_default_with_monogram_fallback(tmp_path, monkeypatch):
    """RATIFIED 2026-07-06: seals are the default badge visual (the operator
    asked for agency logos throughout). Files are keyed by MONOGRAM so every
    agency-string variant shares one seal; uncached agencies keep the designed
    monogram tile; the style stays one-line reversible."""
    from agents.reports import views as v
    from agents.reports.document import build_document

    doc = build_document("Testco", searches=_searches(3), qualify=None)

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("LILA_SEALS_DIR", str(empty))
    html = v.render_scoreboard(doc, "internal")
    assert '<img class="vb-seal"' not in html          # no cache -> all monograms
    assert 'class="vb-mono"' in html

    seals = tmp_path / "seals"
    seals.mkdir()
    (seals / "dod.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepng")  # monogram key
    monkeypatch.setenv("LILA_SEALS_DIR", str(seals))
    html = v.render_scoreboard(doc, "internal")
    assert '<img class="vb-seal"' in html and "data:image/png;base64," in html
    assert "vb-mono-cap" in html                       # seal carries its caption
    # agencies without a cached seal fall back to the monogram tile
    assert 'class="vb-mono">GSA</span>' in html

    monkeypatch.setattr(v, "AGENCY_BADGE_STYLE", "monogram")  # still reversible
    assert '<img class="vb-seal"' not in v.render_scoreboard(doc, "internal")


def _self_contained(html: str) -> bool:
    """Single-file check: no external ASSETS beyond the existing Google Fonts
    imports (inline data: URIs are fine). Anchor hrefs are navigation, not
    assets — live source links are a feature, not a violation."""
    import re
    for url in re.findall(r'\bsrc\s*=\s*["\'](https?://[^"\']+)', html):
        return False  # any external script/img/frame src breaks single-file
    for url in re.findall(r'<link[^>]+href\s*=\s*["\'](https?://[^"\']+)', html):
        if not ("fonts.googleapis.com" in url or "fonts.gstatic.com" in url):
            return False
    return True


def test_legacy_client_view_stays_internal_even_when_gate_clean(
        tmp_path, monkeypatch):
    """The compatibility view cannot bypass canonical LILA release state."""
    from agents.reports.document import build_document
    from agents.reports.views import render_assessment

    client = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr("agents.reports.release.assess_approval_for_release",
                        lambda *a, **k: (None, "approved", []))
    doc = build_document("Acme Federal", searches=_searches(3), qualify=None)
    art = os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.client.html")
    with open(art, "w", encoding="utf-8") as f:
        f.write(_replace_fixture_federal_urls(
            render_assessment(doc, "client")))

    r = client.get(f"/client/{SLUG}/download/foa.html")
    assert r.status_code == 409
    assert r.get_json()["do_not_send"] is True
    html = Path(art).read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") and _self_contained(html)
    assert "AVI-SPL" in html                       # full detail: client view
    assert "INTERNAL REVIEW" not in html           # never the working document
    assert "vb-ticker" not in html                 # no dashboard animation


def test_foa_download_refuses_do_not_send_only(tmp_path, monkeypatch):
    """A stamped-only state surfaces as DO-NOT-SEND and never downloads or
    exports a PDF (the exporter's refusal rules stand)."""
    client = _seed(tmp_path, monkeypatch)
    bad = os.path.join(srv.REPORT_DIR,
                       f"{SLUG}.federal_opportunity_assessment.DO-NOT-SEND.html")
    open(bad, "w").write("<html>stamped</html>")

    r = client.get(f"/client/{SLUG}/download/foa.html")
    assert r.status_code == 409
    assert r.get_json()["do_not_send"] is True

    r2 = client.post("/api/export/pdf", json={"slug": SLUG, "kind": "foa"})
    assert r2.status_code == 409
    assert r2.get_json()["do_not_send"] is True    # refused BEFORE any Chrome
    assert not [p for p in os.listdir(srv.REPORT_DIR) if p.endswith(".pdf")]


def test_newer_do_not_send_supersedes_older_clean_view(tmp_path, monkeypatch):
    """2026-07-10: a failed rebuild can never expose the prior clean file."""
    client = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(
        srv, "_assess_release_gate_for_slug",
        lambda _slug: ({"client": "Acme Federal"}, "approved", []),
    )
    clean = os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.client.html")
    blocked = os.path.join(
        srv.REPORT_DIR, f"{SLUG}.assessment.client.DO-NOT-SEND.html")
    open(clean, "w").write("<html>OLDER CLEAN</html>")
    open(blocked, "w").write("<html>NEWER BLOCKED</html>")
    os.utime(clean, (100, 100))
    os.utime(blocked, (200, 200))

    response = client.get(f"/client/{SLUG}/download/foa.html")
    assert response.status_code == 409
    assert response.get_json()["do_not_send"] is True


def test_newer_blocked_build_supersedes_stable_release(tmp_path, monkeypatch):
    """2026-07-10: stable RELEASE cannot hide any newer failed FOA build."""
    client = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(
        srv, "_assess_release_gate_for_slug",
        lambda _slug: ({"client": "Acme Federal"}, "approved", []),
    )
    stable = os.path.join(
        srv.REPORT_DIR, f"{SLUG}.federal_opportunity_assessment.html")
    qa = os.path.join(
        srv.REPORT_DIR, f"{SLUG}.federal_opportunity_assessment.qa.json")
    full_blocked = os.path.join(
        srv.REPORT_DIR,
        f"{SLUG}.federal_opportunity_assessment.DO-NOT-SEND.html")
    view_blocked = os.path.join(
        srv.REPORT_DIR, f"{SLUG}.assessment.client.DO-NOT-SEND.html")
    open(stable, "w").write("<html>OLDER STABLE RELEASE</html>")
    open(qa, "w").write('{"state":"release"}')
    open(full_blocked, "w").write("<html>NEWER FULL FAILURE</html>")
    os.utime(stable, (100, 100))
    os.utime(qa, (110, 110))
    os.utime(full_blocked, (200, 200))

    assert srv._foa_artifact_state(SLUG)["path"] == full_blocked
    assert client.get(f"/client/{SLUG}/download/foa.html").status_code == 409
    assert client.post("/api/export/pdf", json={
        "slug": SLUG, "kind": "foa"}).status_code == 409

    os.remove(full_blocked)
    open(view_blocked, "w").write("<html>NEWER VIEW FAILURE</html>")
    os.utime(view_blocked, (200, 200))
    assert client.get(f"/client/{SLUG}/download/foa.html").status_code == 409
    assert client.post("/api/export/pdf", json={
        "slug": SLUG, "kind": "foa"}).status_code == 409


def test_foa_html_and_pdf_require_current_approval_but_teaser_does_not(
        tmp_path, monkeypatch):
    import tools.export_pdf as ep

    client = _seed(tmp_path, monkeypatch)
    open(os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.client.html"),
         "w").write("<html>FULL</html>")
    open(os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.sales.html"),
         "w").write("<html>TEASER</html>")
    monkeypatch.setattr(
        srv, "_assess_release_gate_for_slug",
        lambda _slug: (None, "invalid", ["Assess sweep evidence changed"]))
    monkeypatch.setattr(
        ep, "export_deliverable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("FOA gate must refuse before exporter/Chrome")))

    html = client.get(f"/client/{SLUG}/download/foa.html")
    pdf = client.post("/api/export/pdf", json={"slug": SLUG, "kind": "foa"})
    teaser = client.get(f"/client/{SLUG}/download/teaser.html")
    assert html.status_code == 409 and pdf.status_code == 409
    assert html.get_json()["do_not_send"] is True
    assert b"TEASER" in teaser.data and teaser.status_code == 200


def test_teaser_download_is_one_click_and_gated(tmp_path, monkeypatch):
    """No artifact -> live render from the gated model, full gate stack; the
    ticker never rides along (animations are dashboard-only)."""
    client = _seed(tmp_path, monkeypatch)
    r = client.get(f"/client/{SLUG}/download/teaser.html")
    assert r.status_code == 200
    html = r.data.decode()
    assert _self_contained(html) and html.startswith("<!DOCTYPE html>")
    for secret in FIXTURE_SECRETS:
        assert secret not in html
    assert "vb-ticker" not in html and "vb-scroll" not in html
    # a built artifact wins over the live render (composed prose, gated at build)
    art = os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.sales.html")
    open(art, "w").write("<html>BUILT-ARTIFACT-MARKER</html>")
    r2 = client.get(f"/client/{SLUG}/download/teaser.html")
    assert b"BUILT-ARTIFACT-MARKER" in r2.data


def test_direct_download_refuses_dead_external_link(tmp_path, monkeypatch):
    from agents.reports.link_integrity import (
        ClientLinkGateOutcome, LinkCheckResult, LinkClass,
    )
    from agents.reports.lint import LintViolation

    client = _seed(tmp_path, monkeypatch)
    url = "https://agency.example.gov/dead"
    art = os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.sales.html")
    with open(art, "w", encoding="utf-8") as handle:
        handle.write(
            f'<html><body><h2>Evidence</h2><a href="{url}">Source</a>'
            "</body></html>")
    monkeypatch.setattr(
        srv, "_audit_client_download_links",
        lambda _html, **_kwargs: ClientLinkGateOutcome(
            results=(LinkCheckResult(
                url=url, classification=LinkClass.DEAD,
                sections=("Evidence",), proof="GET 404 terminal"),),
            violations=(LintViolation(
                rule="external_link_dead",
                detail=f"DEAD external link in section Evidence: {url}",
                excerpt=url,
            ),),
            manual_checks=(),
            claim_warnings=(),
        ),
    )

    response = client.get(f"/client/{SLUG}/download/teaser.html")

    assert response.status_code == 409
    body = response.get_json()
    assert body["do_not_send"] is True
    assert "external_link_dead" in body["error"]
    assert url in body["error"]


def test_direct_download_allows_unverifiable_external_link(
        tmp_path, monkeypatch):
    from agents.reports.link_integrity import (
        ClientLinkGateOutcome, LinkCheckResult, LinkClass,
    )

    client = _seed(tmp_path, monkeypatch)
    url = "https://agency.example.gov/manual"
    art = os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.sales.html")
    with open(art, "w", encoding="utf-8") as handle:
        handle.write(
            f'<html><body><h2>Evidence</h2><a href="{url}">Source</a>'
            "</body></html>")
    monkeypatch.setattr(
        srv, "_audit_client_download_links",
        lambda _html, **_kwargs: ClientLinkGateOutcome(
            results=(LinkCheckResult(
                url=url, classification=LinkClass.UNVERIFIABLE,
                sections=("Evidence",), proof="GET 403 cannot verify"),),
            violations=(),
            manual_checks=(f"{url} · Evidence · GET 403 cannot verify",),
            claim_warnings=(),
        ),
    )

    response = client.get(f"/client/{SLUG}/download/teaser.html")

    assert response.status_code == 200
    assert url.encode() in response.data
    sidecar = (Path(srv.REPORT_DIR)
               / f"{SLUG}.download.teaser.link-integrity.internal.md")
    assert "## MANUAL LINK CHECK" in sidecar.read_text(encoding="utf-8")
    assert url in sidecar.read_text(encoding="utf-8")


def test_dossier_download_runs_federal_construction_lint(
        tmp_path, monkeypatch):
    client = _seed(tmp_path, monkeypatch)

    response = client.get(f"/client/{SLUG}/download/dossier/1.html")

    assert response.status_code == 409
    assert "federal_link_not_builder_derived" in response.get_json()["error"]


def test_dossier_downloads_each_and_all(tmp_path, monkeypatch):
    from agents.reports.lint import lint_whitelabel

    client = _seed(tmp_path, monkeypatch, safe_links=True)
    r = client.get(f"/client/{SLUG}/download/dossier/1.html")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Video Wall Modernization" in html       # rank 1 carries the dossier
    assert 'id="gtmLogoImg"' in html                # house identity
    assert _self_contained(html)
    assert lint_whitelabel(html).ok
    assert "Grade worksheet" in html                # internal rendering reused

    assert client.get(f"/client/{SLUG}/download/dossier/2.html").status_code == 404
    r_all = client.get(f"/client/{SLUG}/download/dossiers.html")
    assert r_all.status_code == 200
    assert b"complete set" in r_all.data


def test_pdf_export_happy_path_via_gated_exporter(tmp_path, monkeypatch):
    """/api/export/pdf goes through export_deliverable — gates before Chrome;
    the subprocess boundary is mocked, the gate logic is real."""
    import tools.export_pdf as ep
    from agents.reports.document import build_document
    from agents.reports.views import render_assessment

    client = _seed(tmp_path, monkeypatch)
    doc = build_document("Acme Federal", searches=_searches(3), qualify=None)
    art = os.path.join(srv.REPORT_DIR, f"{SLUG}.assessment.sales.html")
    with open(art, "w", encoding="utf-8") as f:
        f.write(_replace_fixture_federal_urls(
            render_assessment(doc, "sales")))

    def fake_export(html_path, pdf_path=None, chrome=None, timeout=120):
        out = os.path.splitext(html_path)[0] + ".pdf"
        open(out, "wb").write(b"%PDF-1.4 fake")
        return out

    monkeypatch.setattr(ep, "export_pdf", fake_export)
    r = client.post("/api/export/pdf", json={"slug": SLUG, "kind": "teaser"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["pdf"].endswith(".assessment.sales.pdf")


def test_sales_mode_without_assessment_renders_no_teaser(tmp_path, monkeypatch):
    client = _seed(tmp_path, monkeypatch, with_assessment=False)
    monkeypatch.setattr(srv, "SALES_VIEW_ENABLED", True)  # flag-gated route
    r = client.get(f"/client/{SLUG}/sales")
    assert r.status_code == 200
    assert b"No assessment has run" in r.data
    assert b"data-locked" not in r.data

    # unknown client is a 404, not a crash
    assert client.get("/client/nope/sales").status_code == 404


def test_banner_get_never_adopts_marks_or_starts_views(tmp_path, monkeypatch):
    """Opening a client is observational even when an adoptable mark waits."""
    client = _seed(tmp_path, monkeypatch)
    for env, name in (("LILA_SEALS_DIR", "seals"), ("LILA_COMPANY_MARKS_DIR", "co"),
                      ("LILA_CLIENT_MARKS_DIR", "cl"), ("LILA_BRAND_DROP_DIR", "drop")):
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        monkeypatch.setenv(env, str(d))
    monkeypatch.setenv("LILA_REVIEW_DIR", srv.REVIEW_DIR)
    (tmp_path / "drop" / f"{SLUG} logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)

    drop = tmp_path / "drop" / f"{SLUG} logo.png"
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("banner GET started a job")

    monkeypatch.setattr(srv, "start_job", forbidden)
    body = client.get(f"/api/client/{SLUG}/banner").get_json()
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert body["marks_adopted"] == []
    assert body["refresh_jobs"] == []
    assert drop.exists()
    assert not (tmp_path / "cl" / f"{SLUG}.png").exists()
    assert after == before
