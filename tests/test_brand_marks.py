"""Brand marks: agency seals + company logos, cached locally, embedded at
render time with monogram fallback. No network — the fetch boundary is
monkeypatched; renders read fixture files from temp cache dirs."""

from __future__ import annotations

import base64
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.brand_marks import (  # noqa: E402
    company_domain_guess, company_slug, fetch_mark,
)

# a real 1x1 PNG; distinct trailing bytes make distinct data URIs per file
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def test_company_slug_and_domain_guess():
    assert company_slug("THE BOEING COMPANY") == "boeing"
    assert company_slug("LEIDOS, INC.") == "leidos"
    assert company_slug("AVI-SPL") == "avi_spl"
    assert company_slug("GENERAL DYNAMICS INFORMATION TECHNOLOGY, INC.") \
        == "general_dynamics_information_technology"
    assert company_domain_guess("THE BOEING COMPANY") == "boeing.com"
    assert company_domain_guess("AVI-SPL") == "avispl.com"


def test_fetch_mark_writes_rejects_placeholders_never_overwrites(tmp_path, monkeypatch):
    calls = []

    class R:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        calls.append(url)
        return R(b"X" * (2000 if "real.gov" in url else 10))

    monkeypatch.setattr("httpx.get", fake_get)
    dest = tmp_path / "dod.png"
    assert fetch_mark("real.gov", dest) is True
    assert dest.read_bytes() == b"X" * 2000
    # placeholder-sized responses are rejected, no file written
    assert fetch_mark("tiny.gov", tmp_path / "tiny.png") is False
    assert not (tmp_path / "tiny.png").exists()
    # existing files always win: no second fetch for dod.png
    n = len(calls)
    assert fetch_mark("real.gov", dest) is True
    assert len(calls) == n


def test_marks_embed_throughout_and_never_identify_locked_competitors(tmp_path, monkeypatch):
    seals, comps = tmp_path / "seals", tmp_path / "company"
    seals.mkdir()
    comps.mkdir()
    dod_bytes, avi_bytes = _PNG + b"DOD", _PNG + b"AVI"
    (seals / "dod.png").write_bytes(dod_bytes)
    (comps / "avi_spl.png").write_bytes(avi_bytes)
    monkeypatch.setenv("LILA_SEALS_DIR", str(seals))
    monkeypatch.setenv("LILA_COMPANY_MARKS_DIR", str(comps))

    from agents.reports.document import build_document
    from agents.reports.views import render_assessment, render_scoreboard
    from tests.test_assessment_document import _searches
    doc = build_document("Testco", searches=_searches(3), qualify=None,
                         as_of=date(2026, 7, 6))

    internal = render_assessment(doc, "internal")
    banner = render_scoreboard(doc, "internal")
    # badges: cached agency renders the seal with a monogram caption;
    # uncached agencies keep the designed monogram tile
    assert 'class="vb-seal"' in banner and "vb-mono-cap" in banner
    assert 'class="vb-mono">GSA</span>' in banner
    # inline marks: agency map + pursuit board rows
    assert internal.count('class="v-amark"') >= 2
    assert _uri(dod_bytes) in internal

    client = render_assessment(doc, "client")
    assert _uri(avi_bytes) in client            # competitor mark, client view

    sales = render_assessment(doc, "sales")
    # agency marks are verifier-tier: visible in sales (strip + badges)
    assert 'class="v-vmark"' in sales
    assert _uri(dod_bytes) in sales
    # a company mark identifies the competitor: the locked name can never
    # hit the cache, so the URI is physically absent from sales HTML
    assert _uri(avi_bytes) not in sales


def test_find_mark_extension_priority(tmp_path):
    from tools.brand_marks import find_mark
    (tmp_path / "dod.jpg").write_bytes(_PNG)
    assert find_mark(tmp_path, "dod").name == "dod.jpg"
    (tmp_path / "dod.png").write_bytes(_PNG)      # png outranks jpg
    assert find_mark(tmp_path, "dod").name == "dod.png"
    assert find_mark(tmp_path, "doj") is None


def _mark_dirs(tmp_path, monkeypatch, roster=()):
    import json
    dirs = {}
    for env, name in (("LILA_SEALS_DIR", "seals"),
                      ("LILA_COMPANY_MARKS_DIR", "company"),
                      ("LILA_CLIENT_MARKS_DIR", "client"),
                      ("LILA_BRAND_DROP_DIR", "drop"),
                      ("LILA_REVIEW_DIR", "review"),
                      ("LILA_INTAKE_DIR", "intake")):
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        monkeypatch.setenv(env, str(d))
        dirs[name] = d
    for slug, cname in roster:
        (dirs["review"] / f"{slug}.review.json").write_text(
            json.dumps({"client_name": cname}))
    return dirs


def test_adopt_recognizes_drops_and_reports_the_rest(tmp_path, monkeypatch):
    from tools.brand_marks import adopt_marks
    d = _mark_dirs(tmp_path, monkeypatch)
    seals, comps = d["seals"], d["company"]

    (seals / "Department of Defense seal.png").write_bytes(_PNG + b"NEW")
    (seals / "dod.jpg").write_bytes(_PNG + b"OLD")     # replaced by the drop
    (seals / "GSA logo.JPG").write_bytes(_PNG)
    (seals / "Mystery Bureau.png").write_bytes(_PNG)   # unrecognized: left
    (seals / "photo.heic").write_bytes(b"x")           # unsupported: left
    (comps / "Boeing logo.jpeg").write_bytes(_PNG)
    (seals / "NAMING.txt").write_text("doc")           # never touched

    rep = adopt_marks()
    assert (seals / "dod.png").read_bytes() == _PNG + b"NEW"
    assert not (seals / "dod.jpg").exists()            # stale variant removed
    assert (seals / "gsa.jpg").exists()                # ext kept, case fixed
    assert (comps / "boeing.jpeg").exists()
    assert (seals / "Mystery Bureau.png").exists()     # never deleted
    assert any("Mystery Bureau" in s for s in rep["skipped"])
    assert any("heic" in s.lower() for s in rep["skipped"])
    assert [a["kind"] for a in rep["adopted"]].count("agency") == 2
    assert len(rep["adopted"]) == 3
    # idempotent: a second sweep adopts nothing
    assert adopt_marks()["adopted"] == []


def test_adopt_routes_client_logos_wherever_they_land(tmp_path, monkeypatch):
    """The Thinklogical case: a client logo dropped at the TOP LEVEL of the
    Desktop folder — or into Company Logos — still reaches the client cache;
    a client on the board can never masquerade as a competitor."""
    from tools.brand_marks import adopt_marks
    d = _mark_dirs(tmp_path, monkeypatch,
                   roster=[("thinklogical", "Thinklogical"),
                           ("recorded_future", "Recorded Future")])

    (d["drop"] / "thinklogical.png").write_bytes(_PNG + b"TL")   # top level
    (d["company"] / "Recorded Future logo.jpg").write_bytes(_PNG)  # wrong folder
    (d["drop"] / "who is this.png").write_bytes(_PNG)            # ambiguous: left

    rep = adopt_marks()
    assert (d["client"] / "thinklogical.png").read_bytes() == _PNG + b"TL"
    assert (d["client"] / "recorded_future.jpg").exists()
    assert not (d["company"] / "recorded_future.jpg").exists()
    assert (d["drop"] / "who is this.png").exists()              # not guessed
    assert any("who is this" in s for s in rep["skipped"])
    kinds = {a["key"]: a["kind"] for a in rep["adopted"]}
    assert kinds == {"thinklogical": "client", "recorded_future": "client"}


def test_desktop_links_point_into_the_cache(tmp_path, monkeypatch):
    from tools.brand_marks import ensure_desktop_links, write_naming_readmes
    d = _mark_dirs(tmp_path, monkeypatch)
    desk = tmp_path / "Desktop"
    made = ensure_desktop_links(desk)
    assert (desk / "Brand Marks" / "Agency Seals").resolve() == d["seals"].resolve()
    assert (desk / "Brand Marks" / "Company Logos").resolve() == d["company"].resolve()
    assert (desk / "Brand Marks" / "Client Logos").resolve() == d["client"].resolve()
    assert len(made) == 3
    assert ensure_desktop_links(desk) == []            # idempotent
    write_naming_readmes()
    assert "dod.png" in (d["seals"] / "NAMING.txt").read_text()
    assert "boeing.png" in (d["company"] / "NAMING.txt").read_text()
    assert "thinklogical" in (d["client"] / "NAMING.txt").read_text()


def test_client_mark_renders_raster_inside_svg_wrapper(tmp_path, monkeypatch):
    """Raster client logos satisfy the identity gate (svg element required
    inside .cover-logo) via the <svg><image> wrapper — the gate is intact."""
    from agents.reports.capture_brief import _client_mark
    from agents.reports.lint import lint_brief_identity
    from agents.reports.document import build_document
    from agents.reports.views import render_assessment
    from tests.test_assessment_document import _searches
    d = _mark_dirs(tmp_path, monkeypatch, roster=[("testco", "Testco")])

    (d["client"] / "testco.png").write_bytes(_PNG)
    mark = _client_mark("Testco")
    assert mark.startswith('<svg class="client-mark')
    assert 'data-brand-asset="1"' in mark  # R13-exempt brand asset
    assert "data:image/png;base64," in mark

    doc = build_document("Testco", searches=_searches(3), qualify=None)
    html = render_assessment(doc, "client")
    assert 'class="client-mark' in html
    assert lint_brief_identity(html).ok

    (d["client"] / "testco.png").unlink()              # no mark -> monogram, gate holds
    assert lint_brief_identity(render_assessment(doc, "client")).ok


def test_dashboard_logo_endpoints(tmp_path, monkeypatch):
    """The dashboard chrome serves both logos: GTM from the committed asset,
    the client mark from the cache (404 -> UI favicon fallback)."""
    import ui.server as srv
    client = srv.app.test_client()

    gtm = client.get("/api/marks/gtm")
    assert gtm.status_code == 200 and gtm.mimetype == "image/png"
    assert len(gtm.data) > 600                     # a real logo, not a stub

    cl = tmp_path / "client"
    cl.mkdir()
    monkeypatch.setenv("LILA_CLIENT_MARKS_DIR", str(cl))
    assert client.get("/api/marks/client/thinklogical").status_code == 404
    (cl / "thinklogical.png").write_bytes(_PNG + b"TLLOGO")
    r = client.get("/api/marks/client/thinklogical")
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert r.data == _PNG + b"TLLOGO"
    assert client.get("/api/marks/client/..%2fetc").status_code in (400, 404)


def test_command_center_agency_and_company_mark_endpoints(tmp_path, monkeypatch):
    """Command Center resolves official agency seals and imported company marks
    from local caches only; unknown identities fail visibly instead of serving
    a broken or guessed image."""
    import ui.server as srv
    seals, companies = tmp_path / "seals", tmp_path / "companies"
    seals.mkdir()
    companies.mkdir()
    (seals / "treas.png").write_bytes(_PNG + b"TREAS")
    (companies / "boeing.png").write_bytes(_PNG + b"BOEING")
    monkeypatch.setenv("LILA_SEALS_DIR", str(seals))
    monkeypatch.setenv("LILA_COMPANY_MARKS_DIR", str(companies))
    client = srv.app.test_client()

    seal = client.get("/api/marks/agency/TREASURY%2C%20DEPARTMENT%20OF%20THE")
    assert seal.status_code == 200 and seal.mimetype == "image/png"
    assert seal.data.endswith(b"TREAS")
    company = client.get("/api/marks/company/The%20Boeing%20Company")
    assert company.status_code == 200 and company.data.endswith(b"BOEING")
    assert client.get("/api/marks/agency/Unknown%20Office").status_code == 404
    assert client.get("/api/marks/company/Unknown%20Company").status_code == 404


def test_client_logo_prefetch_derives_domain_fills_cache_never_overwrites(tmp_path, monkeypatch):
    """A client's OWN logo auto-fetches into the cache so a report renders a
    real mark, not the monogram — domain from the review packet (website, else
    name-guess), and an operator drop always wins."""
    import json as _json
    from tools.brand_marks import client_domain, prefetch_client_logo
    d = _mark_dirs(tmp_path, monkeypatch,
                   roster=[("osprey_flight_solutions", "Osprey Flight Solutions")])
    # domain: name-guess when no website; an attested website wins
    assert client_domain("osprey_flight_solutions",
                         "Osprey Flight Solutions") == "ospreyflightsolutions.com"
    (d["review"] / "acme.review.json").write_text(
        _json.dumps({"client_name": "Acme", "website": "https://www.acme.io/x"}))
    assert client_domain("acme", "Acme") == "acme.io"
    (d["intake"] / "newco.submission.json").write_text(
        _json.dumps({"client_name": "Newco", "website": "https://newco.example/federal"}))
    assert client_domain("newco", "Newco") == "newco.example"
    # A review packet without a website must not hide the intake website.
    (d["review"] / "intakewins.review.json").write_text(
        _json.dumps({"client_name": "Intake Wins", "strategy": {}}))
    (d["intake"] / "intakewins.submission.json").write_text(
        _json.dumps({"client_name": "Intake Wins",
                     "website": "https://intakewins.example/federal"}))
    assert client_domain("intakewins", "Intake Wins") == "intakewins.example"

    calls = []

    class R:
        content = b"X" * 2000

        def raise_for_status(self):
            pass

    monkeypatch.setattr("httpx.get", lambda url, **kw: calls.append(url) or R())

    assert prefetch_client_logo("Osprey Flight Solutions") is True
    assert (d["client"] / "osprey_flight_solutions.png").exists()
    assert "ospreyflightsolutions.com" in calls[0]
    n = len(calls)
    assert prefetch_client_logo("Osprey Flight Solutions") is True   # already cached
    assert len(calls) == n                                           # no re-fetch
    # an operator-dropped logo wins outright — no fetch at all
    (d["client"] / "acme.svg").write_bytes(b"<svg/>")
    calls.clear()
    assert prefetch_client_logo("Acme") is True and calls == []
