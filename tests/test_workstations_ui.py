"""Real-browser behavior for the top scope-workstation switcher."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC_FIXTURES = {
    "/static/command-center-home.js": (
        "application/javascript", ROOT / "ui" / "static" / "command-center-home.js"),
    "/static/command-center-home.css": (
        "text/css", ROOT / "ui" / "static" / "command-center-home.css"),
    "/static/client-view.js": (
        "application/javascript", ROOT / "ui" / "static" / "client-view.js"),
    "/static/client-view.css": (
        "text/css", ROOT / "ui" / "static" / "client-view.css"),
}


def _chrome():
    from tools.export_pdf import find_chrome
    try:
        return find_chrome()
    except Exception:
        return None


def _row(wid, label, phase, current=False):
    scope = {"all": True} if wid == "all" else {
        "agencies": [label.replace(" + ", " ").split()[0]]}
    return {
        "ref": {"id": wid, "scope": scope, "label": label,
                "created_at": None, "created_from": "test", "archived_at": None},
        "phase": phase,
        "boundary_status": "approved" if current else "absent",
        "is_legacy_current": current,
        "sweep_exists": current,
        "historical_sweep_exists": current,
        "sweep_status": "current" if current else "absent",
        "historical_artifacts": 0,
        "diagnostics": [],
    }


DHS = _row("agency_dhs", "DHS", "assess", True)
ALL = _row("all", "All Federal", "dormant")
ROWS = [DHS, ALL]
PACKET_A = "a" * 64
PACKET_B = "b" * 64


def _foundation_documents():
    return [{
        "label": "Intake submission", "stage": "Shared foundation",
        "kind": "intake", "fmt": "json", "qa_pass": None,
        "path": "data/intake/netscout.submission.json",
        "updated": "2026-07-13T10:00:00Z", "key": "foundation-intake",
        "source": "client-foundation",
    }, {
        "label": "Capability profile", "stage": "Shared foundation",
        "kind": "capability_profile", "fmt": "json", "qa_pass": None,
        "path": "clients/netscout/profile.json",
        "updated": "2026-07-13T10:00:00Z", "key": "foundation-profile",
        "source": "client-foundation",
    }, {
        "label": "Baseline strategy template", "stage": "Shared foundation",
        "kind": "baseline_strategy", "fmt": "md", "qa_pass": None,
        "path": "data/review/netscout.review.md",
        "updated": "2026-07-13T10:00:00Z", "key": "foundation-strategy",
        "source": "client-foundation",
    }, {
        "label": "Client brand mark", "stage": "Shared foundation",
        "kind": "client_mark", "fmt": "png", "qa_pass": None,
        "path": "data/reference/marks/client/netscout.png",
        "updated": "2026-07-13T10:00:00Z", "key": "foundation-mark",
        "source": "client-foundation",
    }]


def _current_payload():
    return {
        "slug": "netscout", "client_name": "NETSCOUT",
        "website": "https://netscout.com", "logo_domain": "netscout.com",
        "workstation_mode": "legacy-current", "workstation": DHS,
        "workstations": ROWS,
        "status": "approved", "stages": {"searched": False},
        "review_approved": False, "review_approval_problems": [],
        "assess_ledger": {"exists": False, "state": "missing",
                          "scope": {"agencies": ["DHS"]}},
        "final_brief": None, "target_approved": False,
        "assess_ready": False, "steps": [], "documents": [], "docs_hidden": 0,
        "foundation_documents": _foundation_documents(),
        "sidebar": {
            "targets": [{"rank": 1, "title": "DHS TARGET SENTINEL",
                         "agency": "DHS", "grade": "A", "deadline": "2026-08-01",
                         "dossier": False}],
            "artifacts": [],
        },
    }


def _actionable_payload(*, target_approved=False):
    payload = json.loads(json.dumps(_current_payload()))
    payload.update({
        "assess_ready": True,
        "review_approved": True,
        "review_approval_problems": [],
        "target_approved": target_approved,
        "stages": {"searched": True},
        "assess_ledger": {
            "exists": True, "state": "current",
            "scope": {"agencies": [{"abbr": "DHS", "name": "DHS"}]},
            "live_actionable": 1, "live_classifications": {},
            "horizon_theses": 0, "partner_paths": 0,
        },
    })
    return payload


def _dormant_payload(rows=ROWS):
    return {
        "slug": "netscout", "client_name": "NETSCOUT",
        "website": "https://netscout.com", "logo_domain": "netscout.com",
        "workstation_mode": "dormant", "workstation": ALL,
        "workstations": rows, "documents": [],
        "foundation_documents": _foundation_documents(),
        "sidebar": {"targets": [], "artifacts": []}, "target_approved": False,
    }


def _early_payload():
    row = _row("agency_dhs", "DHS", "search", True)
    row["sweep_exists"] = False
    row["historical_sweep_exists"] = False
    row["sweep_status"] = "absent"
    rows = [row, ALL]
    return {
        "slug": "netscout", "client_name": "NETSCOUT",
        "website": "https://netscout.com", "logo_domain": "netscout.com",
        "workstation_mode": "legacy-current-early", "workstation": row,
        "workstations": rows, "status": "approved",
        "steps": [{
            "key": "search", "title": "Opportunity search", "state": "ready",
            "summary": "Run the exact DHS search.", "stage": "ASSESS",
            "action": None, "artifacts": [], "detail": {},
        }],
        "documents": [], "foundation_documents": _foundation_documents(),
        "sidebar": {"targets": [], "artifacts": []},
        "target_approved": False,
    }


def _native_strategy_payload(*, approved=False, phase=None):
    phase = phase or ("search" if approved else "configure")
    has_sweep = phase == "assess"
    row = _row("agency_dhs", "DHS", phase)
    row.update({
        "is_legacy_current": False,
        "is_native": True,
        "boundary_status": "approved" if approved else "pending",
        "sweep_exists": has_sweep,
        "historical_sweep_exists": has_sweep,
        "sweep_status": "current" if has_sweep else "absent",
    })
    rows = [row, ALL]
    strategy = {
        "client_name": "NETSCOUT",
        "pursuit_strategy": "Exact DHS boundary.",
        "keywords": [{
            "term": "network observability", "category": "capability",
            "rationale": "Exact client capability.", "origin": "system",
        }],
        "kept_out": [], "inferred_naics": [], "naics_meta": [],
        "kept_out_naics": [], "target_agencies": ["DHS"],
        "set_aside_angles": [], "near_misses": [], "searches": [],
        "confidence": 0.9, "review_gate": "Review exact DHS boundary.",
    }
    status = "approved" if approved else "pending"
    return {
        "slug": "netscout", "client_name": "NETSCOUT",
        "website": "https://netscout.com", "logo_domain": "netscout.com",
        "workstation_mode": "native-early", "workstation": row,
        "workstations": rows, "status": status,
        "strategy_packet_sha256": PACKET_A,
        "steps": [{
            "key": "approve", "title": "Analyst Layer · strategy & search boundary",
            "state": "done" if approved else "ready",
            "summary": "Review the exact DHS strategy.", "stage": "ASSESS",
            "action": None if approved else {"kind": "gate"},
            "artifacts": [],
            "detail": {
                "strategy_md": "", "status": status, "note": "",
                "strategy": strategy, "editable": not approved,
                "search_scope": {"agencies": ["DHS"]},
                "revision_count": 0, "revised_at": None,
            },
        }, {
            "key": "search", "title": "Opportunity search",
            "state": "done" if has_sweep else ("ready" if approved else "waiting"),
            "summary": ("Exact DHS search is current." if has_sweep
                        else "Run the exact DHS search."), "stage": "ASSESS",
            "action": None, "artifacts": [],
            "detail": {"opportunities": [{
                "id": "DHS-REVIEW-1", "title": "DHS REVIEW SENTINEL",
                "agency": "DHS", "type": "Sources sought",
                "deadline": "2026-08-15", "verdict": "monitor",
                "reason": "Current exact-scope search result.",
                "url": "https://sam.gov/opp/dhs-review-1",
            }]} if has_sweep else {},
        }],
        "documents": [], "foundation_documents": _foundation_documents(),
        "sidebar": {"targets": [], "artifacts": []},
        "target_approved": False,
    }


def _held_payload():
    row = _row("agency_dhs", "DHS", "search", True)
    row["sweep_exists"] = False
    row["sweep_status"] = "invalid"
    row["diagnostics"] = ["scope-mismatch-" + "x" * 180]
    rows = [row, ALL]
    return {
        "slug": "netscout", "client_name": "NETSCOUT",
        "website": "https://netscout.com", "logo_domain": "netscout.com",
        "workstation_mode": "legacy-current-held", "workstation": row,
        "workstations": rows, "documents": [],
        "foundation_documents": _foundation_documents(),
        "sidebar": {"targets": [], "artifacts": []}, "target_approved": False,
    }


@pytest.fixture(scope="module")
def browser():
    chrome = _chrome()
    if not chrome:
        pytest.skip("no system Chrome available for workstation browser tests")
    with sync_playwright() as manager:
        try:
            browser = manager.chromium.launch(headless=True, executable_path=chrome)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chrome could not launch: {exc}")
        yield browser
        browser.close()


def _page(browser, *, viewport=(1180, 860), delayed_dhs=None,
          dhs_payload=None, create_response=None, decide_response=None,
          cas_state=None, foundation_markdown=None):
    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    methods = []
    urls = []
    bodies = []
    active_payload = dhs_payload or _current_payload()
    rows = active_payload["workstations"]
    # Tranche 3: the mocked create endpoint. Default = a fresh agency_dhs
    # creation; tests override with duplicate/failure shapes.
    create = create_response or {"status": 201, "body": {
        "created": True,
        "workstation": {"id": "agency_dhs", "scope": {"agencies": ["DHS"]}},
        "location": "/client/netscout/workstation/agency_dhs"}}
    decision = decide_response or {"status": 200, "body": {
        "client_name": "NETSCOUT", "status": "approved",
        "packet_sha256": PACKET_B}}

    def route_request(route):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if parsed.netloc != "lila.test":
            route.abort()
            return
        methods.append((request.method, path))
        urls.append(request.url)
        if request.method == "POST":
            try:
                bodies.append((path, json.loads(request.post_data or "{}")))
            except ValueError:
                bodies.append((path, {}))
        posted = bodies[-1][1] if request.method == "POST" else {}
        if path in STATIC_FIXTURES:
            content_type, fixture_path = STATIC_FIXTURES[path]
            route.fulfill(
                status=200, content_type=content_type,
                body=fixture_path.read_text(encoding="utf-8"))
            return
        # explicit scope creation (POST); everything else stays GET-only
        if (request.method == "POST"
                and path == "/api/client/netscout/workstations"):
            route.fulfill(status=create["status"], content_type="application/json",
                          body=json.dumps(create["body"]))
            return
        if request.method == "POST" and path == "/api/decide":
            if cas_state is not None:
                if posted.get("expected_packet_sha256") != cas_state["sha"]:
                    route.fulfill(status=409, content_type="application/json",
                                  body=json.dumps({
                                      "error": "strategy packet changed in another tab",
                                  }))
                    return
                cas_state["sha"] = PACKET_B
                response = {"client_name": "NETSCOUT", "status": "approved",
                            "packet_sha256": PACKET_B}
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(response))
                return
            route.fulfill(status=decision["status"], content_type="application/json",
                          body=json.dumps(decision["body"]))
            return
        if request.method == "POST" and path in {
                "/api/strategy/revise", "/api/strategy/terms"}:
            if cas_state is not None and (
                    posted.get("expected_packet_sha256") != cas_state["sha"]):
                route.fulfill(status=409, content_type="application/json",
                              body=json.dumps({
                                  "error": "strategy packet changed in another tab",
                              }))
                return
            strategy = next((
                (step.get("detail") or {}).get("strategy")
                for step in active_payload.get("steps", [])
                if step.get("key") == "approve"), {}) or {}
            if cas_state is not None:
                cas_state["sha"] = PACKET_B
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({
                              "client_name": "NETSCOUT", "status": "pending",
                              "revision_count": 1, "strategy": strategy,
                              "packet_sha256": PACKET_B,
                          }))
            return
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=html)
            return
        if path == "/api/agencies":
            q = parse_qs(parsed.query).get("q", [""])[0].strip().upper()
            payload = {"matches": [{"abbr": q, "name": q + " (fixture)"}] if q else []}
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(payload))
            return
        if path == "/api/clients" or path == "/api/depository":
            payload = []
        elif path == "/api/client/netscout/workstations":
            payload = {"schema_version": "1", "client_name": "NETSCOUT",
                       "workstations": rows}
        elif path == "/api/client/netscout/workstation/agency_dhs":
            if delayed_dhs and delayed_dhs[0]:
                time.sleep(delayed_dhs[1])
            payload = active_payload
        elif path == "/api/client/netscout/workstation/all":
            payload = _dormant_payload(rows)
        elif path == "/api/client/slow/workstation/all":
            slow = _row("all", "All Federal", "dormant")
            payload = {
                **_dormant_payload([slow]), "slug": "slow",
                "client_name": "SLOW CLIENT", "workstation": slow,
            }
        elif path == "/api/client/netscout/banner":
            payload = {"html": '<div id="dhs-banner-sentinel">DHS BANNER SENTINEL</div>'}
        elif path == "/api/client/netscout/recompetes":
            payload = {
                "built": True, "generated": "2026-07-13", "window_months": 24,
                "population": 1, "dollar_note": "fixture",
                "attack": [{"recipient": "RECOMPETE SENTINEL", "score": 80,
                            "pop_end": "2027-01-01", "naics": "541512"}],
                "defend": [],
            }
        elif path == "/api/client/netscout/change-digest":
            payload = {"built": False, "note": "fixture baseline absent"}
        elif path == "/api/client/netscout/competitors":
            payload = {
                "coverage_note": "fixture",
                "competitors": [{
                    "name": "COMPETITOR SENTINEL", "dollars_label": "$1M",
                    "annual_label": "$333K", "agencies": ["DHS"],
                    "naics": ["541512"], "basis": "fixture", "awards": [],
                    "pursuit_ids": [],
                }],
            }
        elif path == "/api/client/netscout/targets":
            payload = {"targets": [], "pursuit_pocs": []}
        elif path == "/api/contacts/summary":
            payload = {"profiles": 0, "observations": 0, "offices": 0,
                       "observed_30d": 0, "review_pending": 0}
        elif path == "/api/contacts":
            payload = {"profiles": [], "order": [], "hidden": 0,
                       "total": 0, "per_page": 50, "page": 1}
        elif path == "/api/arbiter/openai":
            payload = {"active": False, "key_present": False, "masked": ""}
        elif path == "/api/file":
            requested = parse_qs(parsed.query).get("path", [""])[0]
            if requested.endswith(".png"):
                content = (
                    "data:image/png;base64,"
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                    "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
                payload = {"content": content, "encoding": "data-url",
                           "media_type": "image/png"}
            elif requested.endswith(".md"):
                content = foundation_markdown or (
                    "# Shared foundation fixture\n\nBaseline strategy.")
                payload = {"content": content}
            else:
                content = json.dumps({
                    "fixture": "shared foundation", "path": requested,
                })
                payload = {"content": content}
        elif path.startswith("/api/marks/client/"):
            route.fulfill(status=404, body="")
            return
        else:
            payload = {}
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/*", route_request)
    page._lila_methods = methods  # test-only handle
    page._lila_urls = urls
    page._lila_bodies = bodies
    return page


def test_switching_changes_entire_context_and_emits_gets_only(browser):
    page = _page(browser)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector('.scope-segment[aria-current="page"][data-workstation-id="agency_dhs"]')
    assert page.locator("body").get_by_text("DHS TARGET SENTINEL").count() == 1

    page.click('.scope-segment[data-workstation-id="all"]')
    page.wait_for_selector(".workstation-dormant")
    assert page.url.endswith("/client/netscout/workstation/all")
    assert page.locator("body").get_by_text("DHS TARGET SENTINEL").count() == 0
    assert page.locator("#dhs-banner-sentinel").count() == 0
    assert page.locator(".finale").count() == 0
    assert page.locator('.scope-segment[data-workstation-id="all"]').get_attribute(
        "aria-current") == "page"

    # Native link semantics: Enter on the focused segment restores DHS.
    page.focus('.scope-segment[data-workstation-id="agency_dhs"]')
    page.keyboard.press("Enter")
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    assert all(method == "GET" for method, _path in page._lila_methods)
    page.close()


def test_back_forward_and_reload_restore_exact_workstation(browser):
    page = _page(browser)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector('.scope-segment[aria-current="page"][data-workstation-id="agency_dhs"]')
    page.click('.scope-segment[data-workstation-id="all"]')
    page.wait_for_selector(".workstation-dormant")
    page.go_back()
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    page.go_forward()
    page.wait_for_selector(".workstation-dormant")
    assert page.url.endswith("/client/netscout/workstation/all")
    page.reload()
    page.wait_for_selector(".workstation-dormant")
    assert page.locator('.scope-segment[aria-current="page"]').get_attribute(
        "data-workstation-id") == "all"
    page.close()


@pytest.mark.parametrize("mode", ["dormant", "held", "native-early", "full"])
def test_shared_foundation_is_one_separate_area_in_every_workstation_mode(
        browser, mode):
    payloads = {
        "held": _held_payload(),
        "native-early": _native_strategy_payload(),
        "full": _current_payload(),
    }
    page = _page(browser, dhs_payload=payloads.get(mode, _current_payload()))
    workstation_id = "all" if mode == "dormant" else "agency_dhs"
    page.goto(
        f"http://lila.test/client/netscout/workstation/{workstation_id}")
    foundation = page.locator(".client-foundation")
    foundation.wait_for()
    assert foundation.locator(".client-foundation-title").inner_text() == (
        "Shared client foundation · same in every scope")
    links = foundation.locator("[data-foundation-path]")
    expected = _foundation_documents()
    assert links.count() == len(expected)
    assert foundation.evaluate(
        "node => node.closest('.docgrid, #pane-deliverables, .steps') === null")
    assert page.locator(".docgrid [data-foundation-path]").count() == 0
    for index, doc in enumerate(expected):
        href = links.nth(index).get_attribute("href")
        parsed = urlparse(href)
        assert parsed.path == "/api/file"
        assert parse_qs(parsed.query).get("path") == [doc["path"]]

    links.first.click()
    page.wait_for_function("""() => {
      const viewer = document.querySelector('.client-foundation-viewer.show');
      return viewer && viewer.textContent.includes('shared foundation');
    }""")
    file_urls = [url for url in page._lila_urls
                 if urlparse(url).path == "/api/file"]
    assert parse_qs(urlparse(file_urls[-1]).query).get("path") == [
        expected[0]["path"]]

    links.last.click()
    image = foundation.locator(".client-foundation-viewer.show img")
    image.wait_for()
    assert image.get_attribute("src").startswith("data:image/png;base64,")
    assert image.get_attribute("alt") == "Client brand mark"
    page.close()


@pytest.mark.parametrize("width", [320, 390])
def test_shared_foundation_fits_phone_widths(browser, width):
    page = _page(browser, viewport=(width, 800),
                 dhs_payload=_native_strategy_payload())
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".client-foundation")
    geometry = page.evaluate("""(vw) => {
      const section = document.querySelector('.client-foundation');
      const right = section.getBoundingClientRect().right;
      return {
        sectionRight: right,
        sectionOverflow: section.scrollWidth > section.clientWidth + 1,
        childOverflow: [...section.querySelectorAll('*')].some(node =>
          node.getBoundingClientRect().right > right + 0.5),
        documentOverflow: document.documentElement.scrollWidth > vw + 1,
      };
    }""", width)
    assert geometry["sectionRight"] <= width + 0.5
    assert geometry["sectionOverflow"] is False
    assert geometry["childOverflow"] is False
    assert geometry["documentOverflow"] is False
    page.close()


def test_shared_markdown_strips_active_html_and_non_http_links(browser):
    hostile = """<h1>Safe heading</h1>
<p><strong>kept bold</strong></p>
<img src=x onerror="window.__foundationPwned = 1">
<script>window.__foundationPwned = 2</script>
<iframe srcdoc="<script>parent.__foundationPwned = 3</script>"></iframe>
<a class="bad-link" href="javascript:window.__foundationPwned=4"
 onclick="window.__foundationPwned=5">bad link</a>
<a class="good-link" href="https://example.com/ok?x=1"
 onclick="window.__foundationPwned=6">safe link</a>
"""
    page = _page(
        browser, dhs_payload=_native_strategy_payload(),
        foundation_markdown=hostile)
    # The test harness blocks all external hosts, including the Marked CDN.
    # Identity parsing isolates the sanitizer itself with hostile parser output.
    page.add_init_script("window.marked = {parse: source => source}")
    page.add_init_script("window.__foundationPwned = 0")
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".client-foundation")
    page.locator('[data-foundation-path$=".review.md"]').click()
    viewer = page.locator(".client-foundation-viewer.show")
    viewer.locator("h1:has-text('Safe heading')").wait_for()

    assert viewer.locator("strong:has-text('kept bold')").count() == 1
    assert viewer.locator("img,script,iframe").count() == 0
    links = viewer.locator("a")
    assert links.count() == 2
    assert links.nth(0).inner_text() == "bad link"
    assert links.nth(0).get_attribute("href") is None
    assert links.nth(0).get_attribute("onclick") is None
    assert links.nth(1).inner_text() == "safe link"
    assert links.nth(1).get_attribute("href") == "https://example.com/ok?x=1"
    assert links.nth(1).get_attribute("target") == "_blank"
    assert set((links.nth(1).get_attribute("rel") or "").split()) == {
        "noopener", "noreferrer"}
    assert page.evaluate("window.__foundationPwned") == 0
    page.close()


def test_slow_old_response_cannot_paint_new_selection(browser):
    page = _page(browser)
    payload = json.dumps(_current_payload())
    page.add_init_script(script=f"""
      (() => {{
        const nativeFetch = window.fetch.bind(window);
        const delayedPayload = {payload};
        window.__delayDhs = false;
        window.fetch = (input, init) => {{
          const path = new URL(String(input), location.href).pathname;
          if (window.__delayDhs && path === '/api/client/netscout/workstation/agency_dhs') {{
            return new Promise(resolve => {{
              window.__releaseDhs = () => resolve(new Response(
                JSON.stringify(delayedPayload),
                {{status: 200, headers: {{'Content-Type': 'application/json'}}}}));
            }});
          }}
          return nativeFetch(input, init);
        }};
      }})();
    """)
    page.goto("http://lila.test/client/netscout/workstation/all")
    page.wait_for_selector(".workstation-dormant")
    page.evaluate("window.__delayDhs = true")
    page.evaluate("() => { openWorkstation('netscout', 'agency_dhs', {history:'push'}); }")
    page.wait_for_function("() => typeof window.__releaseDhs === 'function'")
    page.evaluate("""() => {
      openWorkstation('netscout', 'all', {history:'push'});
    }""")
    page.wait_for_selector(".workstation-dormant")
    page.evaluate("window.__releaseDhs()")
    page.wait_for_timeout(100)
    assert page.url.endswith("/client/netscout/workstation/all")
    assert page.locator(".workstation-dormant").count() == 1
    assert page.locator("body").get_by_text("DHS TARGET SENTINEL").count() == 0
    page.close()


def test_slow_catalog_from_older_client_intent_cannot_win(browser):
    page = _page(browser)
    slow = _row("all", "All Federal", "dormant")
    catalog = json.dumps({
        "schema_version": "1", "client_name": "SLOW CLIENT",
        "workstations": [slow],
    })
    page.add_init_script(script=f"""
      (() => {{
        const nativeFetch = window.fetch.bind(window);
        const slowCatalog = {catalog};
        window.fetch = (input, init) => {{
          const path = new URL(String(input), location.href).pathname;
          if (path === '/api/client/slow/workstations') {{
            return new Promise(resolve => {{
              window.__releaseSlowCatalog = () => resolve(new Response(
                JSON.stringify(slowCatalog),
                {{status: 200, headers: {{'Content-Type': 'application/json'}}}}));
            }});
          }}
          return nativeFetch(input, init);
        }};
      }})();
    """)
    page.goto("http://lila.test/client/netscout/workstation/all")
    page.wait_for_selector(".workstation-dormant")
    page.evaluate("() => { select('slow'); }")
    page.wait_for_function(
        "() => typeof window.__releaseSlowCatalog === 'function'")
    page.evaluate("() => { select('netscout'); }")
    page.wait_for_selector(
        '.scope-segment[aria-current="page"][data-workstation-id="all"]')
    page.evaluate("window.__releaseSlowCatalog()")
    page.wait_for_timeout(100)
    assert page.url.endswith("/client/netscout/workstation/all")
    assert page.get_by_text("SLOW CLIENT").count() == 0
    page.close()


def test_early_workstation_has_no_downstream_auxiliary_requests(browser):
    page = _page(browser, dhs_payload=_early_payload())
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".workstation-early")
    assert page.locator(".finale").count() == 0
    assert page.locator("#bannerSlot").count() == 0
    paths = [path for _method, path in page._lila_methods]
    assert "/api/client/netscout" not in paths
    assert "/api/client/netscout/banner" not in paths
    assert "/api/client/netscout/change-digest" not in paths
    assert "/api/arbiter/openai" not in paths
    page.close()


def test_held_workstation_never_borrows_client_global_pipeline(browser):
    page = _page(browser, dhs_payload=_held_payload())
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector("#held-title")
    assert page.locator(".finale").count() == 0
    assert page.locator("#bannerSlot").count() == 0
    paths = [path for _method, path in page._lila_methods]
    assert "/api/client/netscout" not in paths
    assert "/api/client/netscout/banner" not in paths
    assert "/api/client/netscout/change-digest" not in paths
    assert "/api/arbiter/openai" not in paths
    page.close()


def test_full_workstation_qualifies_every_auxiliary_request(browser):
    page = _page(browser, viewport=(1400, 900),
                 dhs_payload=_actionable_payload(target_approved=True))
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector("#dhs-banner-sentinel")
    page.wait_for_selector("#orail:not(.blank)")

    page.evaluate(
        "() => { void openCompetitors('netscout', 'agency_dhs'); }")
    page.wait_for_selector("text=COMPETITOR SENTINEL")
    page.evaluate("closeScopedOverlay()")
    page.evaluate(
        "() => { void openRecompetes('netscout', 'agency_dhs'); }")
    page.wait_for_selector("text=RECOMPETE SENTINEL")

    expected = {
        "/api/client/netscout/banner",
        "/api/client/netscout/recompetes",
        "/api/client/netscout/competitors",
        "/api/client/netscout/targets",
    }
    # Declutter pin (2026-07-24): the change-digest panel is retired, so
    # its auxiliary request must never fire from the page again.
    assert not any(
        urlparse(url).path == "/api/client/netscout/change-digest"
        for url in page._lila_urls)
    for path in expected:
        matching = [url for url in page._lila_urls
                    if urlparse(url).path == path]
        assert matching, path
        assert all(parse_qs(urlparse(url).query).get("workstation_id")
                   == ["agency_dhs"] for url in matching)
    page.close()


def test_navigation_closes_overlay_and_late_detail_cannot_repaint(browser):
    page = _page(browser)
    late = json.dumps({
        "coverage_note": "late fixture",
        "competitors": [{
            "name": "COMPETITOR LATE SENTINEL", "dollars_label": "$1M",
            "annual_label": "$333K", "agencies": ["DHS"],
            "naics": ["541512"], "basis": "late", "awards": [],
            "pursuit_ids": [],
        }],
    })
    page.add_init_script(script=f"""
      (() => {{
        const nativeFetch = window.fetch.bind(window);
        const latePayload = {late};
        window.fetch = (input, init) => {{
          const path = new URL(String(input), location.href).pathname;
          if (path === '/api/client/netscout/competitors') {{
            return new Promise(resolve => {{
              window.__releaseCompetitors = () => resolve(new Response(
                JSON.stringify(latePayload),
                {{status: 200, headers: {{'Content-Type': 'application/json'}}}}));
            }});
          }}
          return nativeFetch(input, init);
        }};
      }})();
    """)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(
        '.scope-segment[aria-current="page"][data-workstation-id="agency_dhs"]')
    page.evaluate(
        "() => { void openCompetitors('netscout', 'agency_dhs'); }")
    page.wait_for_function(
        "() => typeof window.__releaseCompetitors === 'function'")
    page.evaluate(
        "() => { void openWorkstation('netscout', 'all', {history:'push'}); }")
    page.wait_for_selector(".workstation-dormant")
    page.evaluate("window.__releaseCompetitors()")
    page.wait_for_timeout(120)
    assert not page.locator("#compOverlay").evaluate(
        "node => node.classList.contains('show')")
    assert page.get_by_text("COMPETITOR LATE SENTINEL").count() == 0
    page.close()


def test_finale_jobs_keep_origin_and_do_not_refresh_later_selection(browser):
    # 2026-07-24 clean pass: every finale job press is retired (capture
    # brief flag-gated, watchlist and dossier removed). The finale carries
    # the download, the preview, and the one Refresh control only.
    page = _page(browser, viewport=(1400, 900),
                 dhs_payload=_actionable_payload(target_approved=True))
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".btn-one-refresh")
    assert page.query_selector(".btn-final") is None
    assert page.query_selector(".btn-views") is None
    assert page.query_selector(".btn-dossier") is None
    bodies = page.evaluate("window.__runBodies || []")
    assert bodies == []
    page.close()
def test_contacts_has_canonical_history_and_restores_workstation(browser):
    page = _page(browser)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(
        '.scope-segment[aria-current="page"][data-workstation-id="agency_dhs"]')
    page.evaluate("() => { void renderContacts(); }")
    page.wait_for_selector(".hubtabs button.on", state="visible")
    assert page.url.endswith("/?view=contacts")

    page.go_back()
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    page.go_forward()
    page.wait_for_selector(".hubtabs button.on", state="visible")
    assert page.url.endswith("/?view=contacts")
    page.click(".hubtabs button:first-child")
    page.wait_for_selector(".cc2-shell")
    assert page.url == "http://lila.test/"
    page.go_back()
    page.wait_for_selector(".hubtabs button.on", state="visible")
    assert page.url.endswith("/?view=contacts")
    page.close()


def test_slow_contacts_response_cannot_replace_workstation(browser):
    page = _page(browser)
    contacts = json.dumps({
        "profiles": [], "order": [], "hidden": 0,
        "total": 0, "per_page": 50, "page": 1,
    })
    page.add_init_script(script=f"""
      (() => {{
        const nativeFetch = window.fetch.bind(window);
        const contactsPayload = {contacts};
        window.fetch = (input, init) => {{
          const path = new URL(String(input), location.href).pathname;
          if (path === '/api/contacts') {{
            return new Promise(resolve => {{
              window.__releaseContacts = () => resolve(new Response(
                JSON.stringify(contactsPayload),
                {{status: 200, headers: {{'Content-Type': 'application/json'}}}}));
            }});
          }}
          return nativeFetch(input, init);
        }};
      }})();
    """)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    page.evaluate("() => { void renderContacts(); }")
    page.wait_for_function("() => typeof window.__releaseContacts === 'function'")
    page.go_back()
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    page.evaluate("window.__releaseContacts()")
    page.wait_for_timeout(120)
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    assert page.get_by_text("DHS TARGET SENTINEL").count() == 1
    assert page.locator(".hubtabs button.on").count() == 0
    page.close()


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("mode", ["full", "early"])
def test_active_workstation_has_no_phone_document_overflow(browser, width, mode):
    payload = _current_payload() if mode == "full" else _early_payload()
    selector = ".btn-one-refresh" if mode == "full" else ".workstation-early"
    page = _page(browser, viewport=(width, 780), dhs_payload=payload)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(selector)
    page.wait_for_timeout(80)
    widths = page.evaluate("""() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      switcher: document.querySelector('.scope-switcher').scrollWidth,
      switcherBox: document.querySelector('.scope-switcher').clientWidth
    })""")
    assert widths["document"] <= widths["viewport"]
    assert widths["switcher"] <= widths["switcherBox"]
    page.close()


@pytest.mark.parametrize("width", [320, 390])
def test_dormant_switcher_has_no_phone_document_overflow(browser, width):
    page = _page(browser, viewport=(width, 780))
    page.goto("http://lila.test/client/netscout/workstation/all")
    page.wait_for_selector(".workstation-dormant")
    widths = page.evaluate("""() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      switcher: document.querySelector('.scope-switcher').scrollWidth,
      switcherBox: document.querySelector('.scope-switcher').clientWidth
    })""")
    assert widths["document"] <= widths["viewport"]
    assert widths["switcher"] <= widths["switcherBox"]
    page.close()


@pytest.mark.parametrize("width", [320, 390])
def test_held_workstation_has_no_phone_document_overflow(browser, width):
    page = _page(browser, viewport=(width, 780), dhs_payload=_held_payload())
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    # one-gate law (2026-07-18): the held-view search press is curtained
    # (SHOW_LEGACY_WORKSTATION); the ladder is the surviving ready anchor,
    # and the press's absence is pinned here.
    page.wait_for_selector(".ws-ladder")
    assert page.query_selector(".ws-held-search") is None
    widths = page.evaluate("""() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      switcher: document.querySelector('.scope-switcher').scrollWidth,
      switcherBox: document.querySelector('.scope-switcher').clientWidth
    })""")
    assert widths["document"] <= widths["viewport"]
    assert widths["switcher"] <= widths["switcherBox"]
    page.close()


# ── + New scope creation door (Tranche 3, UI-only) ───────────────────────────

def _posts_to_workstations(page):
    return [m for m in page._lila_methods
            if m == ("POST", "/api/client/netscout/workstations")]


def _create_bodies(page):
    return [b for p, b in page._lila_bodies
            if p == "/api/client/netscout/workstations"]


def _open_dialog(page):
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(
        '.scope-segment[aria-current="page"][data-workstation-id="agency_dhs"]')
    page.click("[data-scope-new]")
    page.wait_for_selector('#newScopeOverlay .modal.newscope[role="dialog"]')


def test_new_scope_control_opens_accessible_dialog(browser):
    page = _page(browser)
    _open_dialog(page)
    dlg = page.locator("#newScopeOverlay .modal.newscope")
    assert dlg.get_attribute("aria-modal") == "true"
    assert dlg.get_attribute("aria-labelledby") == "newScopeTitle"
    assert dlg.get_attribute("aria-describedby") == \
        "newScopeDescription newScopeDisclosure"
    assert page.locator("#newScopeTitle").inner_text().strip() != ""
    page.close()


def test_dialog_traps_focus_and_physically_isolates_background(browser):
    page = _page(browser)
    _open_dialog(page)
    assert page.locator(".top").evaluate("node => node.inert") is True
    assert page.locator(".wrap").evaluate("node => node.inert") is True
    assert page.locator(".top").get_attribute("aria-hidden") == "true"
    assert page.locator(".wrap").get_attribute("aria-hidden") == "true"

    assert page.locator('input[name="ns-scope"][value="all"]').evaluate(
        "node => node === document.activeElement") is True
    page.keyboard.press("Shift+Tab")
    assert page.locator(".ns-create").evaluate(
        "node => node === document.activeElement") is True
    page.keyboard.press("Tab")
    assert page.locator('input[name="ns-scope"][value="all"]').evaluate(
        "node => node === document.activeElement") is True

    page.keyboard.press("Escape")
    assert page.locator("#newScopeOverlay").count() == 0
    assert page.locator(".top").evaluate("node => node.inert") is False
    assert page.locator(".wrap").evaluate("node => node.inert") is False
    assert page.locator(".top").get_attribute("aria-hidden") is None
    assert page.locator(".wrap").get_attribute("aria-hidden") is None
    page.close()


def test_open_and_cancel_writes_nothing(browser):
    page = _page(browser)
    _open_dialog(page)
    page.click(".ns-cancel")
    assert page.locator("#newScopeOverlay").count() == 0        # closed
    assert _posts_to_workstations(page) == []                   # no POST
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    page.close()


def test_escape_closes_and_writes_nothing(browser):
    page = _page(browser)
    _open_dialog(page)
    page.keyboard.press("Escape")
    assert page.locator("#newScopeOverlay").count() == 0
    assert _posts_to_workstations(page) == []
    page.close()


def test_inflight_creation_cannot_masquerade_as_cancelled(browser):
    page = _page(browser)
    page.add_init_script(script="""
      (() => {
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/client/netscout/workstations'
              && String((init || {}).method || 'GET').toUpperCase() === 'POST') {
            return new Promise(resolve => {
              window.__releaseCreateAsDuplicate = () => resolve(new Response(
                JSON.stringify({
                  created: false,
                  workstation: {ref: {id: 'all'}},
                  location: '/client/netscout/workstation/all'
                }), {status: 200, headers: {'Content-Type': 'application/json'}}));
            });
          }
          return nativeFetch(input, init);
        };
      })();
    """)
    _open_dialog(page)
    page.click(".ns-create")
    page.wait_for_function(
        "() => typeof window.__releaseCreateAsDuplicate === 'function'")
    assert page.locator(".ns-cancel").is_disabled()

    page.keyboard.press("Escape")
    assert page.locator("#newScopeOverlay").count() == 1
    assert "in progress" in page.locator(".ns-msg").inner_text().lower()
    page.evaluate("document.querySelector('.ns-cancel').click()")
    page.dispatch_event("#newScopeOverlay", "mousedown")
    assert page.locator("#newScopeOverlay").count() == 1

    page.evaluate("window.__releaseCreateAsDuplicate()")
    page.wait_for_selector(".ns-msg:has-text('already exists')")
    assert page.locator(".ns-cancel").is_enabled()
    page.click(".ns-cancel")
    assert page.locator("#newScopeOverlay").count() == 0
    page.close()


def test_disclosure_names_every_thing_cloning_never_copies(browser):
    page = _page(browser)
    _open_dialog(page)
    disc = page.locator(".ns-disclose")
    assert disc.is_visible()
    txt = disc.inner_text().lower()
    for token in ("complete baseline strategy", "pursuit framing", "keywords",
                  "naics", "kept-out", "target-agency", "set-aside angles",
                  "near-miss", "search-plan inputs", "pending"):
        assert token in txt
    for token in ("approval", "search results", "evidence",
                  "reports", "release", "target"):
        assert token in txt
    page.close()


def test_stale_agency_failure_cannot_overwrite_newer_success(browser):
    page = _page(browser)
    page.add_init_script(script="""
      (() => {
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/agencies') {
            const q = url.searchParams.get('q');
            if (q === 'FIRST') {
              return new Promise((_resolve, reject) => {
                window.__rejectOldAgency = () => reject(
                  new Error('old agency lookup failed'));
              });
            }
            if (q === 'DHS') {
              return Promise.resolve(new Response(JSON.stringify({
                matches: [{abbr: 'DHS', name: 'Department of Homeland Security'}]
              }), {status: 200, headers: {'Content-Type': 'application/json'}}));
            }
          }
          return nativeFetch(input, init);
        };
      })();
    """)
    _open_dialog(page)
    page.check('input[name="ns-scope"][value="agencies"]')
    page.fill("#ns-agency-input", "FIRST")
    page.press("#ns-agency-input", "Enter")
    page.wait_for_function(
        "() => typeof window.__rejectOldAgency === 'function'")
    page.fill("#ns-agency-input", "DHS")
    page.press("#ns-agency-input", "Enter")
    page.wait_for_selector(".ns-chips .ed-chip:has-text('DHS')")
    page.evaluate("window.__rejectOldAgency()")
    page.wait_for_timeout(50)
    assert page.locator(".ns-msg").inner_text() == ""
    page.close()


def test_exact_agency_and_required_baseline_clone_reach_the_request_body(browser):
    page = _page(browser)
    _open_dialog(page)
    page.check('input[name="ns-scope"][value="agencies"]')
    page.wait_for_selector(".ns-agencies:not([hidden])")
    page.fill("#ns-agency-input", "DHS")
    page.press("#ns-agency-input", "Enter")
    page.wait_for_selector(".ns-chips .ed-chip")               # DHS resolved + chipped
    page.click(".ns-create")
    page.wait_for_url("**/client/netscout/workstation/agency_dhs")
    assert _create_bodies(page)[-1] == {
        "scope": {"agencies": ["DHS"]}, "clone_baseline": True}
    page.close()


def test_creation_ui_has_no_no_clone_path_and_always_posts_true(browser):
    page = _page(browser)
    _open_dialog(page)
    assert page.locator(".ns-clone-box").count() == 0
    assert "every new scope starts" in page.locator(
        ".ns-disclose").inner_text().lower()
    page.click(".ns-create")                                   # All Federal default
    page.wait_for_url("**/client/netscout/workstation/agency_dhs")
    assert _create_bodies(page)[-1] == {
        "scope": {"all": True}, "clone_baseline": True}
    page.close()


def test_success_navigates_to_the_exact_returned_location(browser):
    # the location generalizes: a DoD response navigates to DoD, not a hard code
    page = _page(browser, create_response={"status": 201, "body": {
        "created": True,
        "workstation": {"id": "agency_dod", "scope": {"agencies": ["DoD"]}},
        "location": "/client/netscout/workstation/agency_dod"}})
    _open_dialog(page)
    page.click(".ns-create")
    page.wait_for_url("**/client/netscout/workstation/agency_dod")
    page.wait_for_timeout(50)
    assert page.url.endswith("/client/netscout/workstation/agency_dod")
    assert page.locator("#newScopeOverlay").count() == 0       # closed on success
    detail_gets = [url for url in page._lila_urls
                   if urlparse(url).path ==
                   "/api/client/netscout/workstation/agency_dod"]
    assert len(detail_gets) == 1  # one success navigation, never a duplicate GET
    page.close()


def test_failure_stays_visible_and_never_navigates(browser):
    page = _page(browser, create_response={
        "status": 400, "body": {"error": "scope invalid"}})
    _open_dialog(page)
    page.click(".ns-create")
    page.wait_for_selector(".ns-msg:has-text('Not created')")
    assert "scope invalid" in page.locator(".ns-msg").inner_text()
    assert page.locator("#newScopeOverlay").count() == 1       # still open, recoverable
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")   # unchanged
    # the current workstation is not relabeled to a different one
    assert page.locator(
        '.scope-segment[aria-current="page"][data-workstation-id="agency_dhs"]'
    ).count() == 1
    page.close()


def test_duplicate_stays_visible_and_never_falls_through(browser):
    page = _page(browser, create_response={"status": 200, "body": {
        "created": False,
        "workstation": {"id": "agency_dhs", "scope": {"agencies": ["DHS"]}},
        "location": "/client/netscout/workstation/agency_dhs"}})
    _open_dialog(page)
    page.check('input[name="ns-scope"][value="agencies"]')
    page.fill("#ns-agency-input", "DHS")
    page.press("#ns-agency-input", "Enter")
    page.wait_for_selector(".ns-chips .ed-chip")
    page.click(".ns-create")
    page.wait_for_selector(".ns-msg:has-text('already exists')")
    assert page.locator("#newScopeOverlay").count() == 1       # visible + recoverable
    page.close()


def test_switching_stays_get_only_with_the_creation_door_present(browser):
    page = _page(browser)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector("[data-scope-new]")
    page.click('.scope-segment[data-workstation-id="all"]')
    page.wait_for_selector(".workstation-dormant")
    page.focus('.scope-segment[data-workstation-id="agency_dhs"]')
    page.keyboard.press("Enter")
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    # the whole switching path never emitted a POST
    assert all(method == "GET" for method, _p in page._lila_methods)
    page.close()


@pytest.mark.parametrize("width", [320, 390])
def test_control_and_dialog_fit_at_phone_widths(browser, width):
    page = _page(browser, viewport=(width, 800))
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector("[data-scope-new]")
    assert page.evaluate(
        "(vw) => document.querySelector('[data-scope-new]')"
        ".getBoundingClientRect().right <= vw + 0.5", width)
    page.click("[data-scope-new]")
    page.wait_for_selector("#newScopeOverlay .modal.newscope")
    overflow = page.evaluate("""(vw) => {
        const m = document.querySelector('#newScopeOverlay .modal.newscope');
        return [...m.querySelectorAll('*')].filter(
            el => el.getBoundingClientRect().right > vw + 0.5).length;
    }""", width)
    assert overflow == 0
    geometry = page.evaluate("""() => ['.ns-radio'].map(selector => {
      const label = document.querySelector(selector);
      const input = label.querySelector('input');
      const text = label.querySelector('span');
      const ir = input.getBoundingClientRect(), tr = text.getBoundingClientRect();
      return {width: ir.width, gap: tr.left - ir.right};
    })""")
    for control in geometry:
        assert control["width"] <= 24
        assert 0 <= control["gap"] <= 24
    page.close()


def test_native_configure_uses_the_early_workstation_surface(browser):
    page = _page(browser, dhs_payload=_native_strategy_payload())
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".workstation-early")
    assert "Shape and approve" in page.locator("#early-title").inner_text()
    assert page.locator("button:has-text('Open Analyst Layer')").count() == 1
    assert page.locator("text=DHS TARGET SENTINEL").count() == 0
    page.close()


def test_native_assess_renders_current_search_as_ready_for_review(browser):
    page = _page(
        browser,
        dhs_payload=_native_strategy_payload(approved=True, phase="assess"))
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".workstation-early")
    assert "Review this scope’s current search" in \
        page.locator("#early-title").inner_text()
    assert page.locator("text=Run this scope’s first current search").count() == 0
    ladder = page.locator(".workstation-early .ws-ladder span")
    assert "on" in (ladder.nth(3).get_attribute("class") or "").split()
    assert "on" not in (ladder.nth(2).get_attribute("class") or "").split()
    context = page.locator(".workstation-early > p").inner_text().lower()
    assert "exact current sweep belongs" in context
    assert "available below" in context
    assert "current search complete" in page.locator("#clientList").inner_text().lower()
    search = page.locator('.step[data-key="search"]')
    assert search.locator(".native-search-review-note").count() == 1
    assert "no metered search" in search.locator(
        ".native-search-review-note").inner_text().lower()
    assert search.locator("button:has-text('Re-run search')").count() == 0
    search.locator("button:has-text('Review')").click()
    assert "DHS REVIEW SENTINEL" in search.locator(".oppwrap").inner_text()
    assert all(method == "GET" for method, _path in page._lila_methods)
    page.close()


def test_native_decision_carries_the_exact_workstation_id(browser):
    page = _page(browser, dhs_payload=_native_strategy_payload())
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".workstation-early")
    page.click("button:has-text('Approve + Run report')")
    page.wait_for_timeout(100)
    bodies = [body for path, body in page._lila_bodies
              if path == "/api/decide"]
    assert bodies[-1]["workstation_id"] == "agency_dhs"
    assert bodies[-1]["expected_packet_sha256"] == PACKET_A
    assert all(path != "/api/strategy/scope" for _method, path in page._lila_methods)
    page.close()


def test_native_decision_failure_is_visible_and_recoverable(browser):
    page = _page(
        browser, dhs_payload=_native_strategy_payload(),
        decide_response={"status": 409, "body": {
            "error": "native packet changed; reload"}})
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".workstation-early")
    approve = page.locator("button:has-text('Approve + Run report')")
    reject = page.locator("button:has-text('Reject')")
    approve.click()
    decision_msg = page.locator('.gate-msg[role="status"]')
    page.wait_for_selector(
        '.gate-msg[role="status"]:has-text("decision not saved")')
    assert "decision not saved" in decision_msg.inner_text()
    assert "native packet changed" in decision_msg.inner_text()
    assert approve.is_enabled()
    assert reject.is_enabled()
    bodies = [body for path, body in page._lila_bodies
              if path == "/api/decide"]
    assert bodies[-1]["workstation_id"] == "agency_dhs"
    assert bodies[-1]["expected_packet_sha256"] == PACKET_A
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    assert all(path != "/api/strategy/scope"
               for _method, path in page._lila_methods)
    page.close()


@pytest.mark.parametrize("approved, endpoint, open_label, save_label", [
    (False, "/api/strategy/revise", "Open Analyst Layer",
     "Save Analyst Layer draft"),
    (True, "/api/strategy/terms", "Open Analyst Layer",
     "Save analyst refinements (approval intact)"),
])
def test_native_strategy_save_carries_exact_workstation_id(
        browser, approved, endpoint, open_label, save_label):
    page = _page(
        browser, dhs_payload=_native_strategy_payload(approved=approved))
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".workstation-early")
    page.click(f"button:has-text('{open_label}')")
    page.click(f"button:has-text('{save_label}')")
    page.wait_for_timeout(100)
    bodies = [body for path, body in page._lila_bodies if path == endpoint]
    assert bodies[-1]["workstation_id"] == "agency_dhs"
    assert bodies[-1]["expected_packet_sha256"] == PACKET_A
    assert all(path != "/api/strategy/scope" for _method, path in page._lila_methods)
    page.close()


def test_native_in_place_vocabulary_rerender_retains_returned_packet_token(
        browser):
    page = _page(browser, dhs_payload=_native_strategy_payload())
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(".workstation-early")
    page.click("button:has-text('Open Analyst Layer')")
    page.click("button:has-text('Approve search vocabulary & review NAICS')")
    page.wait_for_function(
        f"() => DETAIL.strategy_packet_sha256 === '{PACKET_B}'"
        f" && GATE.packetSha256 === '{PACKET_B}'")
    assert "vocabulary saved" in page.locator(
        ".gate-msg:has-text('vocabulary saved')").inner_text()
    bodies = [body for path, body in page._lila_bodies
              if path == "/api/strategy/revise"]
    assert bodies[-1]["workstation_id"] == "agency_dhs"
    assert bodies[-1]["expected_packet_sha256"] == PACKET_A
    page.close()


@pytest.mark.parametrize("approved, endpoint, open_label, save_label", [
    (False, "/api/strategy/revise", "Open Analyst Layer",
     "Save Analyst Layer draft"),
    (True, "/api/strategy/terms", "Open Analyst Layer",
     "Save analyst refinements (approval intact)"),
])
def test_stale_two_tab_strategy_write_keeps_exact_rendered_packet_token(
        browser, approved, endpoint, open_label, save_label):
    # Both tabs render packet A. The first advances the server to packet B;
    # the second must still post A (what it actually rendered), receive a 409,
    # and remain recoverable instead of silently overwriting packet B.
    cas_state = {"sha": PACKET_A}
    pages = [
        _page(browser, dhs_payload=_native_strategy_payload(approved=approved),
              cas_state=cas_state)
        for _ in range(2)
    ]
    for page in pages:
        # Hold the successful save on its in-place state so the assertion can
        # inspect the returned token before the normal 600ms full refresh.
        page.add_init_script(script="""(() => {
          const real = window.setTimeout.bind(window);
          window.setTimeout = (fn, ms, ...args) =>
            ms === 600 ? 0 : real(fn, ms, ...args);
        })();""")
        page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
        page.wait_for_selector(".workstation-early")
        page.click(f"button:has-text('{open_label}')")

    pages[0].click(f"button:has-text('{save_label}')")
    pages[0].wait_for_selector(".gate-msg:has-text('saved')")
    assert cas_state["sha"] == PACKET_B
    assert pages[0].evaluate(
        "() => DETAIL.strategy_packet_sha256") == PACKET_B
    assert pages[0].evaluate("() => GATE.packetSha256") == PACKET_B

    pages[1].click(f"button:has-text('{save_label}')")
    pages[1].wait_for_selector(".gate-msg:has-text('not saved')")
    stale_msg = pages[1].locator(".gate-msg:has-text('not saved')").inner_text()
    assert "changed in another tab" in stale_msg
    assert pages[1].locator(f"button:has-text('{save_label}')").is_enabled()
    assert pages[1].evaluate("() => GATE.packetSha256") == PACKET_A

    first = [body for path, body in pages[0]._lila_bodies if path == endpoint]
    second = [body for path, body in pages[1]._lila_bodies if path == endpoint]
    assert first[-1]["workstation_id"] == "agency_dhs"
    assert second[-1]["workstation_id"] == "agency_dhs"
    assert first[-1]["expected_packet_sha256"] == PACKET_A
    assert second[-1]["expected_packet_sha256"] == PACKET_A
    for page in pages:
        page.close()


def test_browser_back_cannot_hide_an_inflight_creation(browser):
    page = _page(browser)
    page.add_init_script(script="""
      (() => {
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/client/netscout/workstations'
              && String((init || {}).method || 'GET').toUpperCase() === 'POST') {
            // Send the mocked request so the route harness records its exact
            // body, but withhold delivery of the response from the UI until
            // the test explicitly releases it.
            return nativeFetch(input, init).then(response => new Promise(resolve => {
              window.__releaseCreate = () => resolve(response);
            }));
          }
          return nativeFetch(input, init);
        };
      })();
    """)
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector("[data-scope-new]")
    # Establish an in-app history entry behind the origin so Back exercises the
    # popstate path without unloading the test document.
    page.click('.scope-segment[data-workstation-id="all"]')
    page.wait_for_selector(".workstation-dormant")
    page.click('.scope-segment[data-workstation-id="agency_dhs"]')
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    page.click("[data-scope-new]")
    page.click(".ns-create")
    page.wait_for_function("() => typeof window.__releaseCreate === 'function'")
    page.evaluate("history.back()")
    page.wait_for_timeout(100)
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    assert page.locator("#newScopeOverlay").count() == 1
    assert "in progress" in page.locator(".ns-msg").inner_text().lower()
    assert page.locator(".ns-cancel").is_disabled()

    page.evaluate("window.__releaseCreate()")
    page.wait_for_selector("#newScopeOverlay", state="detached")
    page.wait_for_selector("text=DHS TARGET SENTINEL")
    assert page.url.endswith("/client/netscout/workstation/agency_dhs")
    assert page.locator("#newScopeOverlay").count() == 0
    assert _create_bodies(page)[-1]["clone_baseline"] is True
    page.close()


def test_common_agency_quick_toggles_prefill_the_sole_creation_door(browser):
    # Quick-scope chips (2026-07-13): the most common agencies are one-click
    # shortcuts INTO the sole creation door, pre-filled, with the clone
    # disclosure intact — never a second, silent creation path.
    page = _page(browser, create_response={"status": 201, "body": {
        "created": True,
        "workstation": {"id": "agency_dod", "scope": {"agencies": ["DoD"]}},
        "location": "/client/netscout/workstation/agency_dod"}})
    page.goto("http://lila.test/client/netscout/workstation/agency_dhs")
    page.wait_for_selector(
        '.scope-segment[aria-current="page"][data-workstation-id="agency_dhs"]')
    quick = page.locator(".scope-quick").all_inner_texts()
    assert any("DoD" in t for t in quick) and any("GSA" in t for t in quick)
    # an agency that already has a workstation is a switch segment, never a
    # quick-create chip
    assert not any("DHS" in t for t in quick)
    page.click(".scope-quick:has-text('DoD')")
    page.wait_for_selector('#newScopeOverlay .modal.newscope[role="dialog"]')
    assert page.locator(
        'input[name="ns-scope"][value="agencies"]').is_checked()
    assert "DoD" in page.locator(".ns-chips").inner_text()
    assert "complete baseline strategy" in page.locator(
        ".ns-disclose").inner_text().lower()
    # it creates through the same door: explicit clone, exact scope
    page.click(".ns-create")
    page.wait_for_url("**/client/netscout/workstation/agency_dod")
    body = _create_bodies(page)[-1]
    assert body == {"scope": {"agencies": ["DoD"]}, "clone_baseline": True}
    page.close()
